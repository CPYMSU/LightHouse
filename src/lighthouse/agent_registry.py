from __future__ import annotations

from typing import Any

from .agent_bus import _json
from .agent_coordination import (
    execution_profile_for_role,
    merge_work_payload,
    prepare_work_order_payload,
    semantic_terms,
)
from .scalable_agent_bus import ScalablePostgresAgentBus


_ROLE_ALIASES = {
    "design": "architecture",
    "coding": "backend",
    "verification": "wiring-verification",
}
_ACTIVE_WORK = ("queued", "leased", "running", "waiting_dependency", "waiting_confirmation")


class AgentBus2Registry(ScalablePostgresAgentBus):
    """Concrete Agent Bus with one clear, code-capable professional registry."""

    ADDITIONAL_AGENTS: tuple[dict[str, Any], ...] = (
        {
            "name": "architecture-agent",
            "display_name": "Architecture Agent",
            "role": "architecture",
            "specialty": "System boundaries, module relationships, entropy reduction and refactor tradeoffs",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.git.status.v1", "system.git.diff.v1", "tools.search.v1",
                "tools.inspect.v1", "project.contract.inspect.v1", "project.finding.store.v1",
            ],
            "quality_profile": {"entropy_reduction": True, "alternative_analysis": True},
            "max_concurrency": 4,
        },
        {
            "name": "data-agent",
            "display_name": "Data Agent",
            "role": "data",
            "specialty": "Schema, SQL, data consistency, transactions and query performance",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.git.diff.v1", "system.test.run.v1", "data.schema.inspect.v1",
                "data.sql.query.v1", "data.sql.exec.v1",
            ],
            "quality_profile": {"transaction_evidence": True, "data_consistency": True},
            "max_concurrency": 4,
        },
        {
            "name": "security-agent",
            "display_name": "Security Agent",
            "role": "security",
            "specialty": "Authorization, secrets, injection, data exposure and destructive-operation review",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.git.diff.v1", "system.test.run.v1", "data.schema.inspect.v1",
                "data.sql.query.v1", "project.finding.store.v1",
            ],
            "quality_profile": {"least_authority": True, "secret_detection": True},
            "max_concurrency": 4,
        },
        {
            "name": "reality-agent",
            "display_name": "Reality Agent",
            "role": "reality",
            "specialty": "Real files, active entry points, Git state, services and environment evidence",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.git.status.v1", "system.git.diff.v1", "system.service.status.v1",
                "system.journal.read.v1", "project.finding.store.v1",
            ],
            "quality_profile": {"reality_first": True, "no_invented_state": True},
            "max_concurrency": 6,
        },
        {
            "name": "release-agent",
            "display_name": "Release Agent",
            "role": "release",
            "specialty": "Versioning, installers, CI, release contracts and deployment evidence",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.file.patch.v1", "system.git.status.v1", "system.git.diff.v1",
                "system.test.run.v1", "system.service.status.v1", "system.service.restart.v1",
            ],
            "quality_profile": {"cross_platform": True, "release_receipts": True},
            "max_concurrency": 2,
        },
    )

    def register_builtin_agents(self) -> None:
        super().register_builtin_agents()
        # Keep historical rows and legacy scripted registration compatible, but
        # remove the three tool-less generic Agents from Agent Bus 2.0 routing.
        with self._connect() as connection:
            connection.execute(
                """UPDATE lh_agents SET active=FALSE,updated_at=now()
                   WHERE name IN ('design-agent','coding-agent','verification-agent')"""
            )
            rows = connection.execute(
                """SELECT id,role,metadata FROM lh_agents
                   WHERE active=TRUE AND execution_mode='model'"""
            ).fetchall()
            for row in rows:
                metadata = dict(row.get("metadata") or {})
                metadata["execution_profile"] = execution_profile_for_role(row["role"])
                metadata["agent_bus_version"] = "2.0"
                connection.execute(
                    "UPDATE lh_agents SET metadata=%s::jsonb,updated_at=now() WHERE id=%s",
                    (_json(metadata), row["id"]),
                )

        for item in self.ADDITIONAL_AGENTS:
            value = dict(item)
            display_name = value.pop("display_name")
            specialty = value.pop("specialty")
            quality_profile = value.pop("quality_profile")
            role = str(value["role"])
            agent = self.register_agent(
                execution_mode="model",
                visibility="foreground",
                metadata={
                    "execution_profile": execution_profile_for_role(role),
                    "agent_bus_version": "2.0",
                },
                **value,
            )
            with self._connect() as connection:
                connection.execute(
                    """UPDATE lh_agents SET display_name=%s,specialty=%s,
                       quality_profile=%s::jsonb,updated_at=now() WHERE id=%s""",
                    (display_name, specialty, _json(quality_profile), agent["id"]),
                )

    def dispatch(self, *, role: str, **kwargs: Any) -> dict[str, Any]:
        requested_role = str(role or "").strip()
        mapped = _ROLE_ALIASES.get(requested_role, requested_role)
        payload = dict(kwargs.get("payload") or {}) if isinstance(kwargs.get("payload"), dict) else {}
        if mapped != requested_role:
            payload.setdefault("legacy_role_alias", requested_role)
            payload.setdefault("resolved_role", mapped)
        workspace_id = str(kwargs.get("workspace_id") or "")
        parent_run_id = str(kwargs.get("parent_run_id") or "") or None
        goal = str(kwargs.get("goal") or "")
        prepared = prepare_work_order_payload(mapped, goal, payload)
        candidate = self._semantic_duplicate(
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            role=mapped,
            goal=goal,
            payload=prepared,
        )
        if candidate:
            prepared["shared_findings"] = self.shared_findings(
                workspace_id=workspace_id,
                parent_run_id=parent_run_id,
                limit=30,
            )
            merged = merge_work_payload(dict(candidate.get("payload") or {}), prepared)
            updated = self.update_work_payload(candidate["id"], merged)
            self.append_work_event(
                candidate["id"],
                "work_deduplicated",
                {
                    "requested_by": kwargs.get("requested_by"),
                    "goal": goal,
                    "mode": "semantic",
                    "similarity": candidate["similarity"],
                    "context_merged": True,
                },
            )
            updated["deduplicated"] = True
            updated["deduplication_mode"] = "semantic"
            updated["similarity"] = candidate["similarity"]
            return updated
        values = {key: value for key, value in kwargs.items() if key != "payload"}
        return super().dispatch(role=mapped, payload=payload, **values)

    def publish_findings(self, work_order_id: str, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        work = self.get_work_order(work_order_id)
        existing = self.shared_findings(
            workspace_id=work["workspace_id"],
            parent_run_id=work.get("parent_run_id"),
            limit=120,
        )
        for item in findings[:60]:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            subject = str(item.get("subject") or metadata.get("subject") or "").strip()
            position = item.get("value", item.get("position", metadata.get("value", metadata.get("position"))))
            if not subject or position is None:
                continue
            for prior in existing:
                prior_metadata = prior.get("metadata") if isinstance(prior.get("metadata"), dict) else {}
                prior_subject = str(prior.get("subject") or prior_metadata.get("subject") or "").strip()
                prior_position = prior.get(
                    "value",
                    prior.get("position", prior_metadata.get("value", prior_metadata.get("position"))),
                )
                if prior_subject == subject and prior_position is not None and prior_position != position:
                    self.append_work_event(
                        work_order_id,
                        "agent_conflict",
                        {
                            "kind": "finding",
                            "subject": subject,
                            "positions": [
                                {
                                    "work_order_id": prior.get("work_order_id"),
                                    "role": prior.get("source_agent"),
                                    "value": prior_position,
                                },
                                {
                                    "work_order_id": work_order_id,
                                    "role": work.get("role"),
                                    "value": position,
                                },
                            ],
                            "severity": "important",
                            "requires": "main_ai_decision",
                        },
                    )
                    break
        return super().publish_findings(work_order_id, findings)

    def run_activity(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None,
        after_id: int = 0,
        limit: int = 160,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT e.id,e.event_type,e.payload,e.created_at,
                          w.id AS work_order_id,w.role,w.status AS work_status
                   FROM lh_work_events e
                   JOIN lh_work_orders w ON w.id=e.work_order_id
                   WHERE w.workspace_id=%s
                     AND w.parent_run_id IS NOT DISTINCT FROM %s
                     AND e.id>%s
                     AND e.event_type IN ('agent_tool_started','agent_tool_completed')
                   ORDER BY e.id ASC LIMIT %s""",
                (
                    workspace_id,
                    parent_run_id,
                    max(0, int(after_id)),
                    max(1, min(int(limit), 500)),
                ),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_type": row["event_type"],
                "payload": row["payload"],
                "created_at": row["created_at"].isoformat(),
                "work_order_id": str(row["work_order_id"]),
                "role": row["role"],
                "work_status": row["work_status"],
            }
            for row in rows
        ]

    def quality_profiles(self, *, workspace_id: str | None = None) -> list[dict[str, Any]]:
        work_clause = "WHERE workspace_id=%s" if workspace_id else ""
        event_clause = "AND w.workspace_id=%s" if workspace_id else ""
        work_params = [workspace_id] if workspace_id else []
        event_params = [workspace_id] if workspace_id else []
        with self._connect() as connection:
            work_rows = connection.execute(
                f"""SELECT role,
                            count(*) AS total,
                            count(*) FILTER (WHERE status='succeeded') AS succeeded,
                            count(*) FILTER (WHERE status='failed') AS failed,
                            count(*) FILTER (
                              WHERE status='succeeded' AND result IS NOT NULL
                                AND (result ? 'completion_evidence' OR result ? 'receipts')
                            ) AS evidenced
                     FROM lh_work_orders {work_clause}
                     GROUP BY role ORDER BY role""",
                work_params,
            ).fetchall()
            tool_rows = connection.execute(
                f"""SELECT w.role,
                            count(*) AS total_tools,
                            count(*) FILTER (WHERE e.payload->>'status'='succeeded') AS successful_tools,
                            count(*) FILTER (WHERE e.payload->>'status'='failed') AS failed_tools
                     FROM lh_work_events e JOIN lh_work_orders w ON w.id=e.work_order_id
                     WHERE e.event_type='agent_tool_completed' {event_clause}
                     GROUP BY w.role ORDER BY w.role""",
                event_params,
            ).fetchall()
        tools = {row["role"]: row for row in tool_rows}
        profiles = []
        for row in work_rows:
            terminal = int(row["succeeded"] or 0) + int(row["failed"] or 0)
            tool = tools.get(row["role"])
            total_tools = int(tool["total_tools"] or 0) if tool else 0
            profiles.append(
                {
                    "role": row["role"],
                    "completed_work": terminal,
                    "recent_reliability": (
                        round(int(row["succeeded"] or 0) / terminal, 4) if terminal else None
                    ),
                    "evidence_rate": (
                        round(int(row["evidenced"] or 0) / int(row["succeeded"] or 1), 4)
                        if int(row["succeeded"] or 0)
                        else None
                    ),
                    "tool_success_rate": (
                        round(int(tool["successful_tools"] or 0) / total_tools, 4)
                        if tool and total_tools
                        else None
                    ),
                    "failed_tools": int(tool["failed_tools"] or 0) if tool else 0,
                    "advisory_only": True,
                }
            )
        return profiles

    def resource_advice(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        value = super().resource_advice(workspace_id=workspace_id)
        value["quality_profiles"] = self.quality_profiles(workspace_id=workspace_id)
        return value

    def _semantic_duplicate(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None,
        role: str,
        goal: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        goal_terms = semantic_terms(goal)
        assignment = payload.get("assignment") if isinstance(payload.get("assignment"), dict) else {}
        scope = assignment.get("scope") if isinstance(assignment.get("scope"), dict) else {}
        paths = set(str(item) for item in scope.get("paths") or [])
        symbols = set(str(item) for item in scope.get("symbols") or [])
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM lh_work_orders
                   WHERE workspace_id=%s AND role=%s
                     AND parent_run_id IS NOT DISTINCT FROM %s
                     AND status = ANY(%s)
                   ORDER BY updated_at DESC LIMIT 100""",
                (workspace_id, role, parent_run_id, list(_ACTIVE_WORK)),
            ).fetchall()
        best: dict[str, Any] | None = None
        for row in rows:
            existing_payload = dict(row.get("payload") or {})
            existing_assignment = (
                existing_payload.get("assignment")
                if isinstance(existing_payload.get("assignment"), dict)
                else {}
            )
            existing_scope = (
                existing_assignment.get("scope")
                if isinstance(existing_assignment.get("scope"), dict)
                else {}
            )
            existing_terms = semantic_terms(str(row.get("goal") or ""))
            union = goal_terms.union(existing_terms)
            goal_score = len(goal_terms.intersection(existing_terms)) / len(union) if union else 0.0
            existing_paths = set(str(item) for item in existing_scope.get("paths") or [])
            existing_symbols = set(str(item) for item in existing_scope.get("symbols") or [])
            path_score = 1.0 if paths and paths.intersection(existing_paths) else 0.0
            symbol_score = 1.0 if symbols and symbols.intersection(existing_symbols) else 0.0
            similarity = round(goal_score * 0.65 + path_score * 0.2 + symbol_score * 0.15, 4)
            sufficiently_grounded = path_score > 0 or symbol_score > 0 or goal_score >= 0.9
            if sufficiently_grounded and similarity >= 0.72:
                candidate = {**self._work_dict(row), "similarity": similarity}
                if best is None or similarity > float(best.get("similarity") or 0):
                    best = candidate
        return best
