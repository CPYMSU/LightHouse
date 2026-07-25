from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from .memory import PostgresMemoryFabric as BaseMemoryFabric


class PostgresMemoryFabric(BaseMemoryFabric):
    """Memory Fabric with punctuation-aware retrieval and turn-level context."""

    def __init__(self, dsn: str):
        super().__init__(dsn)
        self.agent_bus = None

    def bind_agent_bus(self, agent_bus) -> None:
        self.agent_bus = agent_bus

    @staticmethod
    def _query_terms(query: str) -> list[str]:
        parts = re.findall(r"[A-Za-z0-9]+|[\u3400-\u9fff]{2,}", str(query or "").lower())
        terms: list[str] = []
        for part in parts:
            if len(part) >= 2 and part not in terms:
                terms.append(part[:80])
        return terms[:16]

    def search_files(self, *, workspace_id: str, query: str, limit: int = 20) -> list[dict[str, Any]]:
        maximum = max(1, min(int(limit), 100))
        terms = self._query_terms(query)
        clauses = ["workspace_id=%s", "active=TRUE"]
        params: list[Any] = [workspace_id]
        if terms:
            clauses.append("(" + " OR ".join(["name ILIKE %s OR canonical_path ILIKE %s OR search_text ILIKE %s"] * len(terms)) + ")")
            for term in terms:
                pattern = f"%{term}%"
                params.extend([pattern, pattern, pattern])
        params.append(maximum)
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT id,canonical_path,relative_path,name,extension,mime_type,size_bytes,
                            modified_at,content_hash,last_opened_at,last_seen_at
                     FROM lh_files WHERE {' AND '.join(clauses)}
                     ORDER BY (last_opened_at IS NOT NULL) DESC,last_opened_at DESC NULLS LAST,
                              last_seen_at DESC LIMIT %s""",
                params,
            ).fetchall()
        return [self._file_dict(row) for row in rows]

    def start_task(self, *, run_id: str, conversation_id: str, goal: str) -> dict[str, Any]:
        """Create an unresolved task; semantic continuation is decided by the main AI."""
        with self._connect() as connection:
            conversation = connection.execute(
                "SELECT * FROM lh_conversations WHERE id=%s",
                (conversation_id,),
            ).fetchone()
            if not conversation:
                raise KeyError("conversation not found")
            task_id = str(uuid4())
            row = connection.execute(
                """INSERT INTO lh_memory_tasks(
                       id,workspace_id,conversation_id,actor,goal,status,
                       subject_locator_id,last_run_id
                   ) VALUES (%s,%s,%s,%s,%s,'active',NULL,%s) RETURNING *""",
                (
                    task_id,
                    conversation["workspace_id"],
                    conversation_id,
                    conversation["actor"],
                    goal,
                    run_id,
                ),
            ).fetchone()
            connection.execute(
                "UPDATE lh_conversations SET active_task_id=%s,updated_at=now() WHERE id=%s",
                (task_id, conversation_id),
            )
        self.schedule_distillation(
            workspace_id=str(conversation["workspace_id"]),
            conversation_id=conversation_id,
            run_id=run_id,
            reason="task_started",
        )
        return dict(row)

    def recent_turns(self, *, conversation_id: str, limit: int = 8) -> list[dict[str, Any]]:
        maximum = max(1, min(int(limit), 16))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,role,content,run_id,metadata,created_at
                   FROM lh_messages WHERE conversation_id=%s
                   ORDER BY id DESC LIMIT %s""",
                (conversation_id, max(32, maximum * 8)),
            ).fetchall()
        ordered = list(reversed(rows))
        turns: list[dict[str, Any]] = []
        current: dict[str, Any] | None = None
        for row in ordered:
            role = str(row["role"])
            message = {
                "id": int(row["id"]),
                "content": row["content"],
                "run_id": str(row["run_id"]) if row.get("run_id") else None,
                "metadata": row.get("metadata") or {},
                "created_at": row["created_at"].isoformat(),
            }
            if role == "user":
                if current is not None:
                    turns.append(current)
                current = {"user": message, "assistant": [], "system": []}
            elif current is not None and role == "assistant":
                current["assistant"].append(message)
            elif current is not None:
                current["system"].append(message)
        if current is not None:
            turns.append(current)
        return turns[-maximum:]

    def conversation_summary(self, conversation_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT summary,entities,relations,uncertainties,distillation_level,
                          source_message_id,source_hash,model,updated_at
                   FROM lh_conversation_summaries WHERE conversation_id=%s""",
                (conversation_id,),
            ).fetchone()
        if not row:
            return {
                "summary": "",
                "entities": [],
                "relations": [],
                "uncertainties": [],
                "distillation_level": 0,
                "source_message_id": None,
                "source_hash": None,
                "model": None,
                "updated_at": None,
            }
        value = dict(row)
        value["updated_at"] = value["updated_at"].isoformat() if value.get("updated_at") else None
        return value

    def context(
        self,
        *,
        workspace_id: str,
        actor: str,
        conversation_id: str | None,
        query: str,
        message_limit: int = 20,
        file_limit: int = 20,
    ) -> dict[str, Any]:
        value = super().context(
            workspace_id=workspace_id,
            actor=actor,
            conversation_id=conversation_id,
            query=query,
            message_limit=message_limit,
            file_limit=file_limit,
        )
        if conversation_id:
            value["recent_turns"] = self.recent_turns(
                conversation_id=conversation_id,
                limit=max(1, min(16, message_limit // 2)),
            )
            value["conversation_summary"] = self.conversation_summary(conversation_id)
        else:
            value["recent_turns"] = []
            value["conversation_summary"] = {
                "summary": "",
                "entities": [],
                "relations": [],
                "uncertainties": [],
                "distillation_level": 0,
            }
        return value

    def distillation_source(
        self,
        *,
        conversation_id: str,
        keep_recent_turns: int = 8,
        max_messages: int = 200,
    ) -> dict[str, Any]:
        recent = self.recent_turns(conversation_id=conversation_id, limit=keep_recent_turns)
        recent_ids = {
            int(message["id"])
            for turn in recent
            for key in ("user",)
            for message in ([turn.get(key)] if turn.get(key) else [])
        }
        for turn in recent:
            recent_ids.update(int(item["id"]) for item in turn.get("assistant") or [])
            recent_ids.update(int(item["id"]) for item in turn.get("system") or [])
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,role,content,run_id,created_at
                   FROM lh_messages WHERE conversation_id=%s
                   ORDER BY id DESC LIMIT %s""",
                (conversation_id, max(16, min(int(max_messages), 1000))),
            ).fetchall()
        ordered = [dict(row) for row in reversed(rows) if int(row["id"]) not in recent_ids]
        source_id = max((int(row["id"]) for row in ordered), default=0)
        serializable = [
            {
                "id": int(row["id"]),
                "role": row["role"],
                "content": row["content"],
                "run_id": str(row["run_id"]) if row.get("run_id") else None,
                "created_at": row["created_at"].isoformat(),
            }
            for row in ordered
        ]
        source_hash = hashlib.sha256(
            json.dumps(serializable, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return {
            "conversation_id": conversation_id,
            "messages": serializable,
            "source_message_id": source_id or None,
            "source_hash": source_hash,
            "recent_turns_preserved": len(recent),
        }

    def store_conversation_distillation(
        self,
        *,
        conversation_id: str,
        workspace_id: str,
        result: dict[str, Any],
        source_message_id: int | None,
        source_hash: str,
        distillation_level: int,
        model: str | None = None,
    ) -> dict[str, Any]:
        summary = str(result.get("summary") or "").strip()
        entities = result.get("entities") if isinstance(result.get("entities"), list) else []
        relations = result.get("relations") if isinstance(result.get("relations"), list) else []
        uncertainties = result.get("uncertainties") if isinstance(result.get("uncertainties"), list) else []
        inferences = result.get("inferences") if isinstance(result.get("inferences"), list) else []
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_conversation_summaries(
                       conversation_id,summary,entities,relations,uncertainties,
                       distillation_level,source_message_id,source_hash,model
                   ) VALUES (%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s,%s,%s)
                   ON CONFLICT (conversation_id) DO UPDATE SET
                     summary=EXCLUDED.summary,
                     entities=EXCLUDED.entities,
                     relations=EXCLUDED.relations,
                     uncertainties=EXCLUDED.uncertainties,
                     distillation_level=GREATEST(
                       lh_conversation_summaries.distillation_level,
                       EXCLUDED.distillation_level
                     ),
                     source_message_id=EXCLUDED.source_message_id,
                     source_hash=EXCLUDED.source_hash,
                     model=EXCLUDED.model,
                     updated_at=now()
                   RETURNING *""",
                (
                    conversation_id,
                    summary,
                    json.dumps(entities, ensure_ascii=False),
                    json.dumps(relations, ensure_ascii=False),
                    json.dumps(uncertainties, ensure_ascii=False),
                    max(0, min(int(distillation_level), 9)),
                    source_message_id,
                    source_hash,
                    model,
                ),
            ).fetchone()
            connection.execute(
                """DELETE FROM lh_world_inferences
                   WHERE workspace_id=%s AND conversation_id=%s AND distillation_level <= %s""",
                (workspace_id, conversation_id, max(0, min(int(distillation_level), 9))),
            )
            for item in inferences[:32]:
                if isinstance(item, str):
                    claim = item
                    confidence = 0.7
                    based_on: list[Any] = []
                elif isinstance(item, dict):
                    claim = str(item.get("claim") or "").strip()
                    confidence = float(item.get("confidence") or 0.7)
                    based_on = item.get("based_on") if isinstance(item.get("based_on"), list) else []
                else:
                    continue
                if not claim:
                    continue
                connection.execute(
                    """INSERT INTO lh_world_inferences(
                           id,workspace_id,conversation_id,claim,confidence,based_on,
                           distillation_level,expires_at
                       ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,now()+interval '30 days')""",
                    (
                        str(uuid4()),
                        workspace_id,
                        conversation_id,
                        claim,
                        max(0.0, min(confidence, 1.0)),
                        json.dumps(based_on, ensure_ascii=False),
                        max(0, min(int(distillation_level), 9)),
                    ),
                )
            connection.execute(
                """UPDATE lh_world_uncertainties SET status='dismissed',updated_at=now()
                   WHERE workspace_id=%s AND conversation_id=%s AND status='open'""",
                (workspace_id, conversation_id),
            )
            for item in uncertainties[:16]:
                if isinstance(item, str):
                    question = item
                    severity = "low"
                    evidence: list[Any] = []
                elif isinstance(item, dict):
                    question = str(item.get("question") or item.get("claim") or "").strip()
                    severity = str(item.get("severity") or "low")
                    evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
                else:
                    continue
                if not question:
                    continue
                if severity not in {"low", "medium", "high"}:
                    severity = "low"
                connection.execute(
                    """INSERT INTO lh_world_uncertainties(
                           id,workspace_id,conversation_id,question,severity,status,evidence
                       ) VALUES (%s,%s,%s,%s,%s,'open',%s::jsonb)""",
                    (
                        str(uuid4()),
                        workspace_id,
                        conversation_id,
                        question,
                        severity,
                        json.dumps(evidence, ensure_ascii=False),
                    ),
                )
        value = dict(row)
        value["conversation_id"] = str(value["conversation_id"])
        value["updated_at"] = value["updated_at"].isoformat()
        return value

    def schedule_distillation(
        self,
        *,
        workspace_id: str,
        conversation_id: str,
        run_id: str | None,
        reason: str,
    ) -> dict[str, Any] | None:
        if self.agent_bus is None:
            return None
        return self.agent_bus.enqueue_background_job(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            job_type="memory.conversation.distill",
            payload={"reason": reason, "requested_level": 2},
            coalesce_key=f"conversation-distill:{conversation_id}",
            priority=15,
            delay_seconds=0.5,
        )

    def project_operation(self, run_id: str, snapshot: dict[str, Any]) -> None:
        super().project_operation(run_id, snapshot)
        conversation = self.conversation_for_run(run_id)
        if conversation:
            self.schedule_distillation(
                workspace_id=conversation["workspace_id"],
                conversation_id=conversation["id"],
                run_id=run_id,
                reason="operation_projected",
            )

    def complete_task(self, run_id: str, *, status: str, summary: str | None) -> None:
        super().complete_task(run_id, status=status, summary=summary)
        conversation = self.conversation_for_run(run_id)
        if conversation:
            self.schedule_distillation(
                workspace_id=conversation["workspace_id"],
                conversation_id=conversation["id"],
                run_id=run_id,
                reason="task_completed",
            )
