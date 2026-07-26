from __future__ import annotations

from typing import Any

from .agent_bus import _json
from .agent_coordination import execution_profile_for_role
from .scalable_agent_bus import ScalablePostgresAgentBus


_ROLE_ALIASES = {
    "design": "architecture",
    "coding": "backend",
    "verification": "wiring-verification",
}


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
        values = {key: value for key, value in kwargs.items() if key != "payload"}
        return super().dispatch(role=mapped, payload=payload, **values)

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
