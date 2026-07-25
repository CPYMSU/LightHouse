from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from lighthouse.agent_bus import PostgresAgentBus
from lighthouse.agent_capabilities import AGENT_BUS_CAPABILITIES
from lighthouse.bootstrap import migration_sql
from lighthouse.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from lighthouse.executors.elastic_agent_bus import ElasticAgentBusExecutor
from lighthouse.mega_project_capabilities import MEGA_PROJECT_CAPABILITIES
from lighthouse.mega_projects import PostgresMegaProjectStore
from lighthouse.models import TargetKind
from lighthouse.repository import PostgresRepository
from lighthouse.tool_registry import PostgresToolRegistry


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def _world(tmp_path):
    suffix = uuid4().hex[:10]
    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(
        name=f"mega-system-{suffix}",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
        },
    )
    workspace = repository.create_workspace(
        name=f"mega-workspace-{suffix}",
        data_target_id=None,
        system_target_id=target.id,
    )
    return repository, target, workspace


def test_tool_registry_and_project_knowledge_are_durable(tmp_path):
    repository, _target, workspace = _world(tmp_path)
    registry = CapabilityRegistry((*DEFAULT_CAPABILITIES, *AGENT_BUS_CAPABILITIES, *MEGA_PROJECT_CAPABILITIES))
    tools = PostgresToolRegistry(DSN)
    projects = PostgresMegaProjectStore(DSN)

    try:
        synced = tools.sync_capabilities(registry.list())
        assert synced == len(registry.list())
        found = tools.search("dispatch many agents", limit=20)
        assert any(item["tool_name"] == "agent.bus.dispatch_many.v1" for item in found)
        recommendation = tools.recommend(
            "investigate a large repository then plan implementation and regression",
            workspace_id=workspace.id,
        )
        assert recommendation["tool_search_available"] is True
        assert recommendation["scale_advice"]["advisory_only"] is True

        project = projects.create_project(
            workspace_id=workspace.id,
            title="Large repository upgrade",
            goal="Investigate, distill, implement and regress without a fixed workflow",
        )
        finding = projects.store_finding(
            project_id=project["id"],
            finding_type="verified_fact",
            domain="repository",
            claim="The runtime uses a durable Agent Bus.",
            confidence=0.98,
            evidence=[{"source": "src/lighthouse/agent_bus.py"}],
        )
        step = projects.create_step(
            project_id=project["id"],
            title="Inspect runtime",
            goal="Trace context and Agent Bus integration",
        )
        checkpoint = projects.checkpoint(
            project_id=project["id"],
            summary="Investigation remains open; the main AI decides what happens next.",
            payload={"decision": "continue_or_plan_freely"},
        )
        detail = projects.inspect_project(project["id"])
        assert detail["workflow_enforced"] is False
        assert detail["main_ai_decides_next_action"] is True
        assert detail["findings"][0]["id"] == finding["id"]
        assert detail["steps"][0]["id"] == step["id"]
        assert detail["latest_checkpoint"]["id"] == checkpoint["id"]
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute("DELETE FROM lh_workspaces WHERE id=%s", (workspace.id,))
            connection.commit()


def test_dispatch_many_creates_logical_work_orders_without_product_cap(tmp_path):
    repository, target, workspace = _world(tmp_path)
    bus = PostgresAgentBus(DSN)
    bus.register_builtin_agents()
    registry = CapabilityRegistry((*DEFAULT_CAPABILITIES, *AGENT_BUS_CAPABILITIES))
    executor = ElasticAgentBusExecutor(
        agent_bus=bus,
        context_compiler=object(),
        repository=repository,
        registry=registry,
    )

    try:
        result = executor.execute(
            registry.get("agent.bus.dispatch_many.v1"),
            target,
            {
                "__workspace_id": workspace.id,
                "actor": "main-ai",
                "project_id": None,
                "work_orders": [
                    {
                        "role": "design" if index % 2 == 0 else "verification",
                        "goal": f"Independent investigation {index}",
                    }
                    for index in range(12)
                ],
            },
        )
        assert result.ok is True
        assert result.result["accepted"] == 12
        assert result.result["logical_agent_population_has_no_product_limit"] is True
        assert len({item["id"] for item in result.result["items"]}) == 12
    finally:
        with psycopg.connect(DSN) as connection:
            connection.execute("DELETE FROM lh_workspaces WHERE id=%s", (workspace.id,))
            connection.commit()
