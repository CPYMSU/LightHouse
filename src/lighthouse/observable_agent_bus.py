from __future__ import annotations

import time
from typing import Any

from .agent_bus import PostgresAgentBus, _json


class ObservablePostgresAgentBus(PostgresAgentBus):
    PROFESSIONAL_AGENTS: tuple[dict[str, Any], ...] = (
        {
            "name": "research-agent",
            "display_name": "Research Agent",
            "role": "research",
            "specialty": "Current mature designs, technical approaches, sources and code patterns",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "research.web.search.v1", "research.web.open.v1",
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "tools.search.v1", "tools.inspect.v1", "project.finding.store.v1",
            ],
            "quality_profile": {"evidence": "required", "current_research": True},
            "max_concurrency": 8,
        },
        {
            "name": "taste-agent",
            "display_name": "Taste Agent",
            "role": "taste",
            "specialty": "Visual hierarchy, grids, typography, spacing, color proportion and design originality",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "research.web.search.v1", "research.web.open.v1",
                "system.file.read.v1", "system.file.search.v1", "system.git.diff.v1",
                "project.finding.store.v1",
            ],
            "quality_profile": {"generic_ai_pattern_detection": True, "context_sensitive": True},
            "max_concurrency": 6,
        },
        {
            "name": "frontend-agent",
            "display_name": "Frontend Agent",
            "role": "frontend",
            "specialty": "Frontend architecture, interaction, accessibility, responsive UI and browser behavior",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.file.write.v1", "system.file.patch.v1", "system.git.diff.v1",
                "system.test.run.v1", "project.batch.update.v1",
            ],
            "quality_profile": {"no_fake_live_data": True, "accessibility": True},
            "max_concurrency": 8,
        },
        {
            "name": "backend-agent",
            "display_name": "Backend Agent",
            "role": "backend",
            "specialty": "APIs, services, repositories, transactions, persistence and error paths",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.file.write.v1", "system.file.patch.v1", "system.git.diff.v1",
                "system.test.run.v1", "data.schema.inspect.v1", "data.sql.query.v1",
                "project.batch.update.v1",
            ],
            "quality_profile": {"transaction_evidence": True, "contract_first": False},
            "max_concurrency": 8,
        },
        {
            "name": "wiring-verification-agent",
            "display_name": "Wiring Verification Agent",
            "role": "wiring-verification",
            "specialty": "UI-to-event-to-API-to-service-to-repository-to-database-to-Receipt verification",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.git.diff.v1", "system.test.run.v1", "data.schema.inspect.v1",
                "data.sql.query.v1", "project.wiring.verify.v1",
            ],
            "quality_profile": {"mock_detection": True, "receipt_required": True},
            "max_concurrency": 6,
        },
        {
            "name": "integration-agent",
            "display_name": "Integration Agent",
            "role": "integration",
            "specialty": "Build Cell integration, contract conflicts, focused regression and merge evidence",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "system.git.status.v1", "system.git.diff.v1", "system.file.read.v1",
                "system.file.search.v1", "system.file.patch.v1", "system.test.run.v1",
                "project.integration.update.v1",
            ],
            "quality_profile": {"incremental_integration": True, "conflict_escalation": True},
            "max_concurrency": 2,
        },
        {
            "name": "test-design-agent",
            "display_name": "Test Design Agent",
            "role": "test-design",
            "specialty": "Regression design from changed behavior, boundaries, history and failure recovery",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "system.git.diff.v1", "system.test.run.v1", "project.finding.store.v1",
            ],
            "quality_profile": {"risk_to_test_mapping": True, "cross_platform": True},
            "max_concurrency": 6,
        },
        {
            "name": "contract-agent",
            "display_name": "Contract Agent",
            "role": "contract",
            "specialty": "Versioned API, data, event, capability and UI contracts",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [
                "system.project.context.v1", "system.file.search.v1", "system.file.read.v1",
                "project.contract.create.v1", "project.contract.inspect.v1",
            ],
            "quality_profile": {"consumer_awareness": True, "compatibility": True},
            "max_concurrency": 4,
        },
    )

    def register_builtin_agents(self) -> None:
        super().register_builtin_agents()
        for item in self.PROFESSIONAL_AGENTS:
            value = dict(item)
            display_name = value.pop("display_name")
            specialty = value.pop("specialty")
            quality_profile = value.pop("quality_profile")
            agent = self.register_agent(**value)
            with self._connect() as connection:
                connection.execute(
                    """UPDATE lh_agents SET display_name=%s,specialty=%s,
                       quality_profile=%s::jsonb,updated_at=now() WHERE id=%s""",
                    (display_name, specialty, _json(quality_profile), agent["id"]),
                )

    def append_work_event(
        self,
        work_order_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                   VALUES (%s,%s,%s::jsonb) RETURNING id,event_type,payload,created_at""",
                (work_order_id, str(event_type), _json(payload or {})),
            ).fetchone()
            connection.execute(
                "UPDATE lh_work_orders SET updated_at=now(),heartbeat_at=now() WHERE id=%s",
                (work_order_id,),
            )
        return {
            "id": int(row["id"]),
            "event_type": row["event_type"],
            "payload": row["payload"],
            "created_at": row["created_at"].isoformat(),
        }

    def report_progress(
        self,
        work_order_id: str,
        *,
        progress: float,
        summary: str,
        criticality: str = "background",
        token_usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        progress = max(0.0, min(float(progress), 1.0))
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_work_orders SET progress=%s,display_summary=%s,
                   token_usage=token_usage || %s::jsonb,heartbeat_at=now(),updated_at=now()
                   WHERE id=%s RETURNING *""",
                (progress, str(summary or ""), _json(token_usage or {}), work_order_id),
            ).fetchone()
            if not row:
                raise KeyError("work order not found")
            connection.execute(
                """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                   VALUES (%s,'agent_progress',%s::jsonb)""",
                (
                    work_order_id,
                    _json({
                        "progress": progress,
                        "summary": summary,
                        "criticality": criticality,
                    }),
                ),
            )
        return self._work_dict(row)

    def mark_waiting_confirmation(
        self,
        work_order_id: str,
        *,
        operation_id: str,
        capability: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_work_orders SET status='waiting_confirmation',
                   display_summary=%s,updated_at=now() WHERE id=%s RETURNING *""",
                (f"Permission required for {capability}", work_order_id),
            ).fetchone()
            if not row:
                raise KeyError("work order not found")
            connection.execute(
                """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                   VALUES (%s,'permission_required',%s::jsonb)""",
                (work_order_id, _json({"operation_id": operation_id, "capability": capability})),
            )
        return self._work_dict(row)

    def agent_for_work_order(self, work_order_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT a.* FROM lh_work_orders w JOIN lh_agents a ON a.id=w.agent_id
                   WHERE w.id=%s""",
                (work_order_id,),
            ).fetchone()
        if not row:
            raise KeyError("agent for work order not found")
        return self._agent_dict(row)

    def work_events(self, work_order_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,event_type,payload,created_at FROM lh_work_events
                   WHERE work_order_id=%s AND id>%s ORDER BY id LIMIT 500""",
                (work_order_id, max(0, int(after_id))),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "event_type": row["event_type"],
                "payload": row["payload"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]

    def wait_many(
        self,
        work_order_ids: list[str],
        *,
        timeout: float = 0,
        critical_roles: list[str] | None = None,
    ) -> dict[str, Any]:
        ids = [str(item) for item in work_order_ids if str(item)]
        if not ids:
            raise ValueError("work_order_ids must not be empty")
        deadline = time.monotonic() + max(0.0, min(float(timeout), 120.0))
        terminal = {"succeeded", "failed", "cancelled", "superseded"}
        while True:
            items = [self.get_work_order(item) for item in ids]
            considered = (
                [item for item in items if item.get("role") in set(critical_roles or [])]
                if critical_roles
                else items
            )
            if considered and all(item.get("status") in terminal for item in considered):
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.25)
        return {
            "items": items,
            "terminal": sum(1 for item in items if item.get("status") in terminal),
            "pending": sum(1 for item in items if item.get("status") not in terminal),
            "waited_for_roles": critical_roles or [],
            "main_ai_decides_after_wait": True,
        }

    def coordination_advice(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        items = self.list_work_orders(
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            limit=100,
        )
        if project_id:
            items = [
                item for item in items
                if str((item.get("payload") or {}).get("project_id") or "") == project_id
            ]
        terminal = {"succeeded", "failed", "cancelled", "superseded"}
        pending = [item for item in items if item.get("status") not in terminal]
        critical_roles = {"backend", "contract", "wiring-verification", "integration"}
        critical_pending = [item for item in pending if item.get("role") in critical_roles]
        critical_events = []
        for item in items:
            for event in self.work_events(item["id"]):
                payload = event.get("payload") or {}
                if payload.get("criticality") == "critical":
                    critical_events.append({"work_order_id": item["id"], **event})
        if critical_events or critical_pending:
            strategy = "wait_for_critical"
            reason = "Critical architecture, wiring or integration evidence is still pending."
        elif pending:
            strategy = "work_and_review_later"
            reason = "Remaining Agents can continue in parallel while the main AI advances independent work."
        elif items:
            strategy = "results_ready"
            reason = "All current Agent work is terminal and can be distilled now."
        else:
            strategy = "no_wait_preference"
            reason = "No delegated Agent work exists for this Run."
        return {
            "recommended_strategy": strategy,
            "reason": reason,
            "critical_pending": [item["id"] for item in critical_pending],
            "other_pending": [item["id"] for item in pending if item not in critical_pending],
            "critical_events": critical_events[-20:],
            "advisory_only": True,
            "main_ai_may_wait_or_continue": True,
            "parallel_build_then_review_supported": True,
        }

    def observatory(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None = None,
    ) -> dict[str, Any]:
        items = self.list_work_orders(
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            limit=100,
        )
        active_states = {"leased", "running", "waiting_dependency", "waiting_confirmation"}
        terminal_states = {"succeeded", "failed", "cancelled", "superseded"}
        return {
            "total": len(items),
            "active": sum(1 for item in items if item.get("status") in active_states),
            "queued": sum(1 for item in items if item.get("status") == "queued"),
            "completed": sum(1 for item in items if item.get("status") in terminal_states),
            "items": items,
        }
