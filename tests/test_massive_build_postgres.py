from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from lighthouse.bootstrap import migration_sql
from lighthouse.massive_build import PostgresMassiveBuildStore
from lighthouse.mega_projects import PostgresMegaProjectStore
from lighthouse.model_usage import PostgresModelUsageStore
from lighthouse.models import TargetKind
from lighthouse.repository import PostgresRepository
from lighthouse.scalable_agent_bus import ScalablePostgresAgentBus


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def _world(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(
        name=f"massive-system-{suffix}",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
        },
    )
    workspace = repository.create_workspace(
        name=f"massive-workspace-{suffix}",
        data_target_id=None,
        system_target_id=target.id,
    )
    return repository, workspace


def test_professional_agents_cells_contracts_leases_and_wiring_are_durable(tmp_path):
    _repository, workspace = _world(tmp_path)
    bus = ScalablePostgresAgentBus(DSN)
    projects = PostgresMegaProjectStore(DSN)
    massive = PostgresMassiveBuildStore(DSN)
    usage = PostgresModelUsageStore(DSN)
    bus.register_builtin_agents()
    try:
        roles = {item["role"] for item in bus.list_agents(include_hidden=True)}
        assert {
            "research", "taste", "frontend", "backend", "wiring-verification",
            "integration", "test-design", "contract",
        }.issubset(roles)

        project = projects.create_project(
            workspace_id=workspace.id,
            title="Warehouse massive build",
            goal="Create and integrate many independently verified code batches",
        )
        contract_v1 = massive.create_contract(
            project_id=project["id"],
            contract_type="api",
            name="procurement",
            schema={"version": 1, "path": "/v1/procurement"},
            status="provisional",
            consumers=["frontend", "backend"],
        )
        contract_v2 = massive.create_contract(
            project_id=project["id"],
            contract_type="api",
            name="procurement",
            schema={"version": 2, "path": "/v2/procurement"},
            status="stable",
            consumers=["frontend", "backend"],
            supersedes_id=contract_v1["id"],
        )
        assert contract_v2["version"] == 2

        cell = massive.create_cell(
            project_id=project["id"],
            name="backend-procurement",
            domain="backend",
            goal="Implement the real procurement service and persistence chain",
            contract_ids=[contract_v2["id"]],
        )
        work_a = bus.dispatch(
            workspace_id=workspace.id,
            requested_by="main-ai",
            role="backend",
            goal="Implement backend batch A",
            payload={"project_id": project["id"], "cell_id": cell["id"], "allow_writes": True},
        )
        work_b = bus.dispatch(
            workspace_id=workspace.id,
            requested_by="main-ai",
            role="backend",
            goal="Implement backend batch B",
            payload={"project_id": project["id"], "cell_id": cell["id"], "allow_writes": True},
        )
        lease = massive.acquire_lease(
            project_id=project["id"],
            cell_id=cell["id"],
            owner_work_order_id=work_a["id"],
            scope_type="directory",
            scope="src/backend",
            lease_seconds=600,
        )
        with pytest.raises(ValueError, match="already leased"):
            massive.acquire_lease(
                project_id=project["id"],
                cell_id=cell["id"],
                owner_work_order_id=work_b["id"],
                scope_type="directory",
                scope="src/backend/api",
                lease_seconds=600,
            )
        assert massive.valid_lease(
            project_id=project["id"],
            owner_work_order_id=work_a["id"],
            path="src/backend/service.py",
        )["id"] == lease["id"]

        batch = massive.create_batch(
            project_id=project["id"],
            cell_id=cell["id"],
            title="Service and repository",
            goal="Implement one reviewable backend capability",
        )
        massive.update_batch(
            batch["id"],
            status="accepted",
            changed_files=["src/backend/service.py"],
            added_lines=1200,
            diff_summary={"behaviors_added": ["create purchase request"]},
            verification={"focused_tests": "passed"},
        )
        integration = massive.create_integration(
            project_id=project["id"],
            title="Backend domain integration",
            source_cells=[cell["id"]],
            source_batches=[batch["id"]],
        )
        massive.update_integration(
            integration["id"],
            status="succeeded",
            result_commit="abc123",
            verification={"integration_tests": "passed"},
        )
        wiring = massive.upsert_wiring(
            project_id=project["id"],
            feature_key="purchase-request-create",
            title="Create purchase request",
            states={
                "frontend_state": "verified",
                "event_state": "verified",
                "api_state": "verified",
                "service_state": "verified",
                "repository_state": "verified",
                "database_state": "verified",
                "receipt_state": "verified",
                "e2e_state": "passed",
                "overall_state": "fully_connected",
            },
            evidence=[{"test": "test_create_purchase_request_e2e"}],
            work_order_id=work_a["id"],
        )
        assert wiring["overall_state"] == "fully_connected"

        usage.record(
            {
                "workspace_id": workspace.id,
                "project_id": project["id"],
                "work_order_id": work_a["id"],
                "agent_id": work_a["agent_id"],
                "provider": "test",
                "model": "model",
                "call_kind": "agent:backend",
                "input_tokens": 1000,
                "output_tokens": 200,
                "total_tokens": 1200,
            }
        )
        assert usage.summary(project_id=project["id"])["total_tokens"] == 1200

        brief = massive.project_brief(project["id"])
        assert brief["workflow_enforced"] is False
        assert brief["main_ai_decides_waiting_and_next_action"] is True
        assert brief["cells"][0]["id"] == cell["id"]
        assert brief["wiring"][0]["overall_state"] == "fully_connected"
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute("DELETE FROM lh_workspaces WHERE id=%s", (workspace.id,))
            connection.commit()
