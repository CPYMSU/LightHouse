from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from lighthouse.agent_registry import AgentBus2Registry
from lighthouse.bootstrap import migration_sql
from lighthouse.models import TargetKind
from lighthouse.repository import PostgresRepository


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def _world(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(
        name=f"agent-bus2-system-{suffix}",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
        },
    )
    workspace = repository.create_workspace(
        name=f"agent-bus2-workspace-{suffix}",
        data_target_id=None,
        system_target_id=target.id,
    )
    return repository, workspace


def test_registry_deduplication_findings_conflicts_and_dependencies_are_durable(tmp_path):
    _repository, workspace = _world(tmp_path)
    bus = AgentBus2Registry(DSN)
    bus.register_builtin_agents()
    try:
        agents = bus.list_agents(include_hidden=True)
        roles = {item["role"] for item in agents}
        assert {
            "research", "architecture", "frontend", "backend", "data", "security",
            "taste", "contract", "test-design", "wiring-verification", "reality",
            "integration", "release",
        }.issubset(roles)
        active_names = {item["name"] for item in agents}
        assert not {"design-agent", "coding-agent", "verification-agent"}.intersection(active_names)
        for item in agents:
            if item["execution_mode"] == "model":
                capabilities = set(item.get("capabilities") or [])
                assert "system.file.read.v1" in capabilities
                assert "system.file.search.v1" in capabilities

        payload = {
            "assignment": {
                "intent": "investigate_and_patch",
                "scope": {"paths": ["src/service.py"], "symbols": ["authorize"]},
                "deliverables": ["root cause", "tests"],
            },
            "intensity": "advanced",
        }
        first = bus.dispatch(
            workspace_id=workspace.id,
            requested_by="main-ai",
            role="backend",
            goal="Fix wildcard scope inheritance",
            payload=payload,
        )
        duplicate = bus.dispatch(
            workspace_id=workspace.id,
            requested_by="main-ai",
            role="backend",
            goal="Fix wildcard scope inheritance",
            payload={**payload, "new_context": "The parent Run uses allowed_capabilities wildcard."},
            priority=80,
        )
        assert duplicate["id"] == first["id"]
        assert duplicate["deduplicated"] is True
        assert duplicate["payload"]["new_context"].startswith("The parent Run")
        assert any(event["event_type"] == "work_deduplicated" for event in bus.work_events(first["id"]))

        second = bus.dispatch(
            workspace_id=workspace.id,
            requested_by="main-ai",
            role="integration",
            goal="Integrate the wildcard fix",
            payload={
                "assignment": {
                    "intent": "integrate",
                    "scope": {"paths": ["src/service.py"]},
                    "deliverables": ["integration evidence"],
                },
                "intensity": "advanced",
            },
        )
        conflicts = (second["payload"]["coordination"].get("conflicts") or [])
        assert conflicts[0]["subject"] == "overlapping_write_intent"
        assert conflicts[0]["with_work_order_id"] == first["id"]
        assert bus.active_conflicts(
            workspace_id=workspace.id,
            parent_run_id=None,
        )[0]["payload"]["kind"] == "write_intent"

        bus.add_dependencies(second["id"], [first["id"]])
        with psycopg.connect(DSN) as connection:
            count = connection.execute(
                "SELECT count(*) FROM lh_work_dependencies WHERE work_order_id=%s AND depends_on_id=%s",
                (second["id"], first["id"]),
            ).fetchone()[0]
        assert count == 1

        bus.publish_findings(
            first["id"],
            [
                {
                    "claim": "Wildcard capabilities must match every Agent-authorized tool.",
                    "status": "verified",
                    "confidence": 1,
                    "evidence": [{"file": "src/lighthouse/background_intelligence.py"}],
                }
            ],
        )
        board = bus.shared_findings(
            workspace_id=workspace.id,
            parent_run_id=None,
        )
        assert board[-1]["claim"].startswith("Wildcard capabilities")
        assert board[-1]["source_agent"] == "backend"
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute("DELETE FROM lh_workspaces WHERE id=%s", (workspace.id,))
            connection.commit()
