from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from lighthouse.agent_registry import AgentBus2Registry
from lighthouse.agent_store import PostgresAgentStore
from lighthouse.bootstrap import migration_sql
from lighthouse.models import KernelMode, TargetKind
from lighthouse.repository import PostgresRepository


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def test_specialist_tool_start_and_result_are_queryable_by_parent_run(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(
        name=f"execution-system-{suffix}",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
        },
    )
    workspace = repository.create_workspace(
        name=f"execution-workspace-{suffix}",
        data_target_id=None,
        system_target_id=target.id,
    )
    run_id = str(uuid4())
    PostgresAgentStore(DSN).create_run(
        run_id=run_id,
        task="observe tools",
        workspace_id=workspace.id,
        actor="adsin",
        mode=KernelMode.AUTO,
        max_steps=48,
        auto_confirm=True,
    )
    bus = AgentBus2Registry(DSN)
    bus.register_builtin_agents()
    try:
        work = bus.dispatch(
            workspace_id=workspace.id,
            parent_run_id=run_id,
            requested_by="main-ai",
            role="backend",
            goal="Inspect app.py",
            payload={
                "assignment": {
                    "scope": {"paths": ["src/app.py"]},
                    "deliverables": ["finding"],
                }
            },
        )
        bus.append_work_event(
            work["id"],
            "agent_tool_started",
            {
                "capability": "system.file.read.v1",
                "label": "READ",
                "summary": "src/app.py",
                "status": "running",
            },
        )
        bus.append_work_event(
            work["id"],
            "agent_tool_completed",
            {
                "capability": "system.file.read.v1",
                "label": "READ",
                "summary": "src/app.py",
                "status": "succeeded",
                "receipt_ok": True,
                "result_hash": "hash-1",
            },
        )

        values = bus.run_activity(
            workspace_id=workspace.id,
            parent_run_id=run_id,
        )

        assert [item["payload"]["status"] for item in values] == ["running", "succeeded"]
        assert all(item["role"] == "backend" for item in values)
        assert values[-1]["payload"]["result_hash"] == "hash-1"
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute("DELETE FROM lh_agent_runs WHERE id=%s", (run_id,))
            connection.execute("DELETE FROM lh_workspaces WHERE id=%s", (workspace.id,))
            connection.commit()
