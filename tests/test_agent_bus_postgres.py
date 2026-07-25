from __future__ import annotations

import os
from uuid import uuid4

import pytest

from lighthouse.agent_bus import PostgresAgentBus
from lighthouse.bootstrap import migration_sql
from lighthouse.models import TargetKind
from lighthouse.repository import PostgresRepository


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def _workspace(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(
        name=f"bus-system-{suffix}",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
        },
    )
    return repository.create_workspace(
        name=f"bus-workspace-{suffix}",
        data_target_id=None,
        system_target_id=target.id,
    )


def test_work_order_is_registered_leased_and_completed(tmp_path):
    workspace = _workspace(tmp_path)
    bus = PostgresAgentBus(DSN)
    bus.register_builtin_agents()

    agents = bus.list_agents(include_hidden=True)
    assert any(item["role"] == "memory-steward" for item in agents)
    assert any(item["role"] == "coding" for item in agents)

    work = bus.dispatch(
        workspace_id=workspace.id,
        requested_by="adsin",
        role="coding",
        goal="分析 index.html 的升級方案",
        payload={"path": str(tmp_path / "index.html")},
        priority=80,
    )
    assert work["status"] == "queued"
    assert work["requested_by"] == "adsin"

    claimed = bus.claim_work_order(
        worker_id="worker-1",
        execution_modes=("model",),
    )
    assert claimed is not None
    assert claimed["id"] == work["id"]
    assert claimed["status"] == "leased"

    running = bus.mark_running(work["id"], worker_id="worker-1")
    assert running["status"] == "running"

    completed = bus.complete(
        work["id"],
        result={"summary": "ready", "evidence": ["index.html"]},
    )
    assert completed["status"] == "succeeded"
    assert completed["result"]["summary"] == "ready"


def test_background_jobs_coalesce_to_latest_payload(tmp_path):
    workspace = _workspace(tmp_path)
    bus = PostgresAgentBus(DSN)
    bus.register_builtin_agents()

    first = bus.enqueue_background_job(
        workspace_id=workspace.id,
        job_type="memory.workspace.scan",
        payload={"roots": [str(tmp_path)], "max_files": 100},
        coalesce_key=f"scan:{workspace.id}",
    )
    second = bus.enqueue_background_job(
        workspace_id=workspace.id,
        job_type="memory.workspace.scan",
        payload={"roots": [str(tmp_path)], "max_files": 500},
        coalesce_key=f"scan:{workspace.id}",
    )
    assert second["id"] == first["id"]
    assert second["payload"]["max_files"] == 500

    claimed = bus.claim_background_job(worker_id="memory-worker")
    assert claimed is not None
    assert claimed["id"] == first["id"]
    assert claimed["status"] == "running"

    completed = bus.complete_background_job(
        claimed["id"],
        result={"indexed": 4},
    )
    assert completed["status"] == "succeeded"
    assert completed["result"]["indexed"] == 4
