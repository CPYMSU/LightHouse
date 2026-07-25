from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any
from uuid import uuid4

from .models import canonical_json


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


class ContextCompiler:
    """Compile small, evidence-rich decision bundles from durable world state."""

    def __init__(self, memory, agent_bus):
        self.memory = memory
        self.agent_bus = agent_bus

    def compile(
        self,
        *,
        workspace_id: str,
        actor: str,
        conversation_id: str | None,
        run_id: str | None,
        query: str,
        force: bool = False,
        turn_limit: int = 8,
        file_limit: int = 16,
    ) -> dict[str, Any]:
        query = str(query or "").strip()
        query_hash = hashlib.sha256(query.encode("utf-8")).hexdigest()
        source_cursor = self._source_cursor(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            query_hash=query_hash,
        )
        if not force:
            cached = self._cached(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                run_id=run_id,
                query_hash=query_hash,
                source_cursor=source_cursor,
            )
            if cached is not None:
                cached["snapshot"] = {
                    **(cached.get("snapshot") or {}),
                    "cache": "hit",
                    "source_cursor": source_cursor,
                }
                return cached

        memory = self.memory.context(
            workspace_id=workspace_id,
            actor=actor,
            conversation_id=conversation_id,
            query=query,
            message_limit=max(16, turn_limit * 3),
            file_limit=file_limit,
        )
        candidates = self._candidate_entities(memory)
        facts = self._verified_facts(workspace_id=workspace_id, memory=memory)
        inferences, uncertainties = self._semantic_state(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
        )
        work_orders = self.agent_bus.list_work_orders(
            workspace_id=workspace_id,
            parent_run_id=run_id,
            limit=20,
        )
        agents = self.agent_bus.list_agents(include_hidden=True)
        summary = memory.get("conversation_summary") or {}
        distillation_level = max(
            1,
            int(summary.get("distillation_level") or 0),
            max((int(item.get("distillation_level") or 0) for item in inferences), default=0),
        )
        bundle: dict[str, Any] = {
            "available": True,
            "current_request": {"content": query},
            "recent_turns": (memory.get("recent_turns") or [])[-max(1, min(int(turn_limit), 16)):],
            "conversation_summary": summary,
            "active_task": memory.get("active_task"),
            "recent_tasks": memory.get("recent_tasks") or [],
            "candidate_entities": candidates,
            "verified_facts": facts,
            "inferences": inferences,
            "uncertainties": uncertainties,
            "relevant_files": memory.get("relevant_files") or [],
            "recent_locators": memory.get("recent_locators") or [],
            "available_agents": agents,
            "work_orders": work_orders,
            "distillation": {
                "level": distillation_level,
                "foreground_source": "context_snapshot",
                "background_upgrade_pending": distillation_level < 2,
            },
            "snapshot": {
                "cache": "miss",
                "source_cursor": source_cursor,
                "compiled_at": _utc_now().isoformat(),
            },
        }
        self._store_snapshot(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            run_id=run_id,
            query_hash=query_hash,
            source_cursor=source_cursor,
            distillation_level=distillation_level,
            payload=bundle,
        )
        if conversation_id and distillation_level < 2:
            self.agent_bus.enqueue_background_job(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                run_id=run_id,
                job_type="memory.conversation.distill",
                payload={"actor": actor, "query": query, "requested_level": 2},
                coalesce_key=f"conversation-distill:{conversation_id}",
                priority=15,
                delay_seconds=0.5,
            )
        return bundle

    def invalidate(self, *, workspace_id: str, conversation_id: str | None = None) -> None:
        with self.memory._connect() as connection:
            if conversation_id:
                connection.execute(
                    "DELETE FROM lh_context_snapshots WHERE workspace_id=%s AND conversation_id=%s",
                    (workspace_id, conversation_id),
                )
            else:
                connection.execute(
                    "DELETE FROM lh_context_snapshots WHERE workspace_id=%s",
                    (workspace_id,),
                )

    def _source_cursor(
        self,
        *,
        workspace_id: str,
        conversation_id: str | None,
        run_id: str | None,
        query_hash: str,
    ) -> str:
        with self.memory._connect() as connection:
            message = None
            task = None
            summary = None
            if conversation_id:
                message = connection.execute(
                    "SELECT max(id) AS id FROM lh_messages WHERE conversation_id=%s",
                    (conversation_id,),
                ).fetchone()
                task = connection.execute(
                    """SELECT t.id,t.updated_at,t.subject_locator_id
                       FROM lh_conversations c LEFT JOIN lh_memory_tasks t ON t.id=c.active_task_id
                       WHERE c.id=%s""",
                    (conversation_id,),
                ).fetchone()
                summary = connection.execute(
                    """SELECT source_message_id,source_hash,distillation_level,updated_at
                       FROM lh_conversation_summaries WHERE conversation_id=%s""",
                    (conversation_id,),
                ).fetchone()
            files = connection.execute(
                """SELECT max(updated_at) AS updated_at,count(*) AS count
                   FROM lh_files WHERE workspace_id=%s AND active=TRUE""",
                (workspace_id,),
            ).fetchone()
            work = None
            if run_id:
                work = connection.execute(
                    """SELECT max(updated_at) AS updated_at,count(*) AS count
                       FROM lh_work_orders WHERE parent_run_id=%s""",
                    (run_id,),
                ).fetchone()
        value = {
            "query": query_hash,
            "message": dict(message) if message else None,
            "task": {**dict(task), "updated_at": _iso(task.get("updated_at"))} if task else None,
            "summary": {**dict(summary), "updated_at": _iso(summary.get("updated_at"))} if summary else None,
            "files": {**dict(files), "updated_at": _iso(files.get("updated_at"))} if files else None,
            "work": {**dict(work), "updated_at": _iso(work.get("updated_at"))} if work else None,
        }
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()

    def _cached(
        self,
        *,
        workspace_id: str,
        conversation_id: str | None,
        run_id: str | None,
        query_hash: str,
        source_cursor: str,
    ) -> dict[str, Any] | None:
        with self.memory._connect() as connection:
            row = connection.execute(
                """SELECT payload FROM lh_context_snapshots
                   WHERE workspace_id=%s
                     AND conversation_id IS NOT DISTINCT FROM %s
                     AND run_id IS NOT DISTINCT FROM %s
                     AND query_hash=%s AND source_cursor=%s
                     AND (expires_at IS NULL OR expires_at > now())
                   ORDER BY created_at DESC LIMIT 1""",
                (workspace_id, conversation_id, run_id, query_hash, source_cursor),
            ).fetchone()
        return dict(row["payload"]) if row else None

    def _store_snapshot(
        self,
        *,
        workspace_id: str,
        conversation_id: str | None,
        run_id: str | None,
        query_hash: str,
        source_cursor: str,
        distillation_level: int,
        payload: dict[str, Any],
    ) -> None:
        with self.memory._connect() as connection:
            connection.execute(
                """INSERT INTO lh_context_snapshots(
                       id,workspace_id,conversation_id,run_id,query_hash,source_cursor,
                       distillation_level,payload,expires_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (workspace_id,conversation_id,run_id,query_hash,source_cursor)
                   DO UPDATE SET
                     distillation_level=GREATEST(
                       lh_context_snapshots.distillation_level,
                       EXCLUDED.distillation_level
                     ),
                     payload=EXCLUDED.payload,
                     created_at=now(),
                     expires_at=EXCLUDED.expires_at""",
                (
                    str(uuid4()),
                    workspace_id,
                    conversation_id,
                    run_id,
                    query_hash,
                    source_cursor,
                    max(0, min(int(distillation_level), 9)),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    _utc_now() + timedelta(minutes=10),
                ),
            )

    @staticmethod
    def _candidate_entities(memory: dict[str, Any]) -> list[dict[str, Any]]:
        values: dict[tuple[str, str], dict[str, Any]] = {}

        def add(kind: str, locator: str, evidence: str, confidence: float) -> None:
            locator = str(locator or "").strip()
            if not locator:
                return
            key = (kind, locator)
            current = values.setdefault(
                key,
                {
                    "type": kind,
                    "locator": locator,
                    "evidence": [],
                    "confidence": confidence,
                },
            )
            if evidence not in current["evidence"]:
                current["evidence"].append(evidence)
            current["confidence"] = max(float(current["confidence"]), float(confidence))

        task = memory.get("active_task") if isinstance(memory.get("active_task"), dict) else {}
        conversation = memory.get("conversation") if isinstance(memory.get("conversation"), dict) else {}
        add(str(task.get("subject_kind") or "file"), str(task.get("subject") or ""), "active_task_subject", 1.0)
        add(
            str(conversation.get("active_subject_kind") or "file"),
            str(conversation.get("active_subject_value") or ""),
            "last_successful_receipt_subject",
            0.95,
        )
        for index, item in enumerate(memory.get("relevant_files") or []):
            add("file", str(item.get("canonical_path") or ""), "relevant_file_index", max(0.35, 0.85 - index * 0.03))
        for index, item in enumerate(memory.get("recent_locators") or []):
            add(str(item.get("kind") or "locator"), str(item.get("canonical_value") or ""), "recent_locator", max(0.25, 0.7 - index * 0.02))
        return sorted(values.values(), key=lambda item: (-float(item["confidence"]), item["locator"]))[:24]

    def _verified_facts(self, *, workspace_id: str, memory: dict[str, Any]) -> list[dict[str, Any]]:
        facts: list[dict[str, Any]] = []
        for item in memory.get("relevant_files") or []:
            path = str(item.get("canonical_path") or "")
            if not path:
                continue
            facts.extend(
                [
                    {
                        "fact": "indexed_file",
                        "subject": path,
                        "value": True,
                        "source": "lh_files",
                        "observed_at": item.get("last_seen_at"),
                        "freshness": "projected",
                    },
                    {
                        "fact": "last_known_content_hash",
                        "subject": path,
                        "value": item.get("content_hash"),
                        "source": "lh_files",
                        "observed_at": item.get("last_seen_at"),
                        "freshness": "projected",
                    },
                ]
            )
        with self.memory._connect() as connection:
            rows = connection.execute(
                """SELECT f.fact_key,f.value,f.source_type,f.source_id,f.evidence,
                          f.observed_at,f.valid_until,f.volatility,
                          e.entity_type,e.canonical_key
                   FROM lh_world_facts f
                   LEFT JOIN lh_world_entities e ON e.id=f.entity_id
                   WHERE f.workspace_id=%s
                     AND (f.valid_until IS NULL OR f.valid_until > now())
                   ORDER BY f.observed_at DESC LIMIT 40""",
                (workspace_id,),
            ).fetchall()
        for row in rows:
            facts.append(
                {
                    "fact": row["fact_key"],
                    "subject": row.get("canonical_key"),
                    "entity_type": row.get("entity_type"),
                    "value": row["value"],
                    "source": row["source_type"],
                    "source_id": row.get("source_id"),
                    "evidence": row.get("evidence") or [],
                    "observed_at": _iso(row.get("observed_at")),
                    "valid_until": _iso(row.get("valid_until")),
                    "volatility": row["volatility"],
                    "freshness": "verified",
                }
            )
        return facts[:64]

    def _semantic_state(
        self,
        *,
        workspace_id: str,
        conversation_id: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        with self.memory._connect() as connection:
            inferences = connection.execute(
                """SELECT claim,confidence,based_on,distillation_level,created_at,expires_at
                   FROM lh_world_inferences
                   WHERE workspace_id=%s
                     AND conversation_id IS NOT DISTINCT FROM %s
                     AND (expires_at IS NULL OR expires_at > now())
                   ORDER BY created_at DESC LIMIT 24""",
                (workspace_id, conversation_id),
            ).fetchall()
            uncertainties = connection.execute(
                """SELECT id,question,severity,status,evidence,created_at,updated_at
                   FROM lh_world_uncertainties
                   WHERE workspace_id=%s
                     AND conversation_id IS NOT DISTINCT FROM %s
                     AND status='open'
                   ORDER BY updated_at DESC LIMIT 16""",
                (workspace_id, conversation_id),
            ).fetchall()
        return (
            [
                {
                    **dict(row),
                    "created_at": _iso(row.get("created_at")),
                    "expires_at": _iso(row.get("expires_at")),
                }
                for row in inferences
            ],
            [
                {
                    **dict(row),
                    "id": str(row["id"]),
                    "created_at": _iso(row.get("created_at")),
                    "updated_at": _iso(row.get("updated_at")),
                }
                for row in uncertainties
            ],
        )
