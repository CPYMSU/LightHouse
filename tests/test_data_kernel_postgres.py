from __future__ import annotations

import os

import psycopg
import pytest

from lighthouse.bootstrap import build_kernel, migration_sql
from lighthouse.config import Settings
from lighthouse.data_kernel import PostgresDataCatalog
from lighthouse.models import KernelMode, OperationRequest, TargetKind
from lighthouse.repository import PostgresRepository
from lighthouse.targets import validate_target_config


DSN = os.environ.get("TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="TEST_DATABASE_URL is not configured")


def request(workspace_id: str, capability: str, arguments: dict, *, key: str) -> OperationRequest:
    return OperationRequest(
        capability=capability,
        arguments=arguments,
        workspace_id=workspace_id,
        actor="integration",
        mode=KernelMode.AUTO,
        idempotency_key=key,
    )


def test_postgresql_catalog_resource_semantic_and_update_chain(monkeypatch):
    monkeypatch.setenv("TEST_DATA_DSN", DSN)
    with psycopg.connect(DSN) as connection:
        connection.execute("DROP SCHEMA IF EXISTS erp CASCADE")
        connection.execute("CREATE SCHEMA erp")
        connection.execute(
            """CREATE TABLE erp.purchase_requests(
                   id TEXT PRIMARY KEY,
                   title TEXT NOT NULL,
                   status TEXT NOT NULL,
                   department TEXT NOT NULL,
                   created_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )"""
        )
        connection.execute(
            """INSERT INTO erp.purchase_requests(id,title,status,department)
               VALUES ('PR-1','Leak detector','pending','research'),
                      ('PR-2','Cloud server','received','operations')"""
        )
        connection.commit()

    repository = PostgresRepository(DSN)
    repository.migrate(migration_sql())
    target = repository.create_target(
        name="integration-erp",
        kind=TargetKind.DATA,
        config=validate_target_config(
            TargetKind.DATA,
            {
                "dsn_env": "TEST_DATA_DSN",
                "allowed_schemas": ["erp"],
                "raw_sql_query": False,
                "raw_sql_exec": False,
                "max_rows": 100,
            },
        ),
    )
    workspace = repository.create_workspace(
        name="integration-company",
        data_target_id=target.id,
        system_target_id=None,
    )
    catalog = PostgresDataCatalog(DSN)
    catalog.bind_target(workspace.id, target.id, "erp", is_default=True)

    # A migration rerun must remain safe after a custom alias is present.
    kernel = build_kernel(Settings(database_url=DSN, api_key="x" * 32), migrate=True)

    synced = kernel.submit(request(workspace.id, "data.catalog.sync.v1", {"target_alias": "erp"}, key="sync"))
    assert synced["receipt"]["ok"] is True
    assert synced["receipt"]["result"]["resources"] == 1

    searched = kernel.submit(
        request(
            workspace.id,
            "data.resource.search.v1",
            {
                "target_alias": "erp",
                "resource": "erp.purchase_requests",
                "filters": {"status": "pending"},
                "order_by": ["id"],
            },
            key="search-pending",
        )
    )
    assert [row["id"] for row in searched["receipt"]["result"]["rows"]] == ["PR-1"]

    policy = kernel.submit(
        request(
            workspace.id,
            "data.resource.policy.v1",
            {
                "target_alias": "erp",
                "resource": "erp.purchase_requests",
                "policy": {
                    "writable_columns": ["status"],
                    "display_column": "title",
                    "default_order": ["-created_at"],
                    "policy": {"max_limit": 50},
                },
            },
            key="policy",
        )
    )
    policy = kernel.confirm(policy["operation"]["id"], actor="integration")
    assert policy["receipt"]["result"]["resource"]["writable_columns"] == ["status"]

    semantic = kernel.submit(
        request(
            workspace.id,
            "data.semantic.register.v1",
            {
                "target_alias": "erp",
                "command": "data.purchase.pending",
                "resource": "erp.purchase_requests",
                "action": "search",
                "definition": {
                    "fixed_filters": {"status": "pending"},
                    "param_filters": {"department": "department"},
                    "columns": ["id", "title", "status", "department"],
                    "order_by": ["id"],
                    "limit": 20,
                },
            },
            key="semantic-register",
        )
    )
    kernel.confirm(semantic["operation"]["id"], actor="integration")

    semantic_result = kernel.submit(
        request(
            workspace.id,
            "data.semantic.query.v1",
            {
                "target_alias": "erp",
                "command": "data.purchase.pending",
                "params": {"department": "research"},
            },
            key="semantic-query",
        )
    )
    assert semantic_result["receipt"]["result"]["rows"][0]["id"] == "PR-1"

    update = kernel.submit(
        request(
            workspace.id,
            "data.resource.update.v1",
            {
                "target_alias": "erp",
                "resource": "erp.purchase_requests",
                "key": "PR-1",
                "changes": {"status": "received"},
            },
            key="update",
        )
    )
    update = kernel.confirm(update["operation"]["id"], actor="integration")
    assert update["receipt"]["result"]["updated"]["status"] == "received"

    empty = kernel.submit(
        request(
            workspace.id,
            "data.semantic.query.v1",
            {
                "target_alias": "erp",
                "command": "data.purchase.pending",
                "params": {"department": "research"},
            },
            key="semantic-query-after-update",
        )
    )
    assert empty["receipt"]["result"]["rows"] == []
