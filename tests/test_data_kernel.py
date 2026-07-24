from __future__ import annotations

from lighthouse.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from lighthouse.data_capabilities import DATA_KERNEL_CAPABILITIES
from lighthouse.data_kernel import DataTargetResolver, InMemoryDataCatalog
from lighthouse.executors.postgres import PostgresExecutor
from lighthouse.kernel import OperationKernel
from lighthouse.models import Capability, ConfirmationMode, ExecutionResult, KernelMode, OperationRequest, Risk, TargetKind
from lighthouse.repository import InMemoryRepository
from lighthouse.targets import validate_target_config


class FakeExecutor:
    def __init__(self):
        self.targets = []

    def execute(self, capability, target, arguments):
        self.targets.append(target.id)
        return ExecutionResult(ok=True, result={"target": target.id})


def test_workspace_data_alias_routes_operation_to_second_world():
    repository = InMemoryRepository()
    first = repository.create_target(name="erp", kind=TargetKind.DATA, config={"dsn_env": "ERP_DSN"})
    second = repository.create_target(name="finance", kind=TargetKind.DATA, config={"dsn_env": "FINANCE_DSN"})
    workspace = repository.create_workspace(name="company", data_target_id=first.id, system_target_id=None)
    catalog = InMemoryDataCatalog()
    catalog.bind_target(workspace.id, first.id, "erp", is_default=True)
    catalog.bind_target(workspace.id, second.id, "finance")
    executor = FakeExecutor()
    registry = CapabilityRegistry((*DEFAULT_CAPABILITIES, *DATA_KERNEL_CAPABILITIES))
    kernel = OperationKernel(repository, registry, {"postgres": executor}, target_resolver=DataTargetResolver(catalog), data_catalog=catalog)
    result = kernel.submit(OperationRequest(capability="data.catalog.resources.v1", arguments={"target_alias": "finance"}, workspace_id=workspace.id, actor="adsin"))
    assert result["receipt"]["result"]["target"] == second.id
    assert executor.targets == [second.id]


def test_catalog_sync_creates_resources_and_preserves_write_policy():
    catalog = InMemoryDataCatalog()
    snapshot = {
        "database": "warehouse",
        "tables": [{"schema": "public", "table": "purchase_requests", "kind": "BASE TABLE", "primary_key": ["id"], "columns": [{"name": "id"}, {"name": "status"}, {"name": "title"}]}],
        "foreign_keys": [],
    }
    catalog.replace_snapshot("target-1", snapshot)
    resource = catalog.update_resource_policy("target-1", "public.purchase_requests", {"writable_columns": ["status"], "policy": {"max_limit": 200}})
    assert resource["writable_columns"] == ["status"]
    catalog.replace_snapshot("target-1", snapshot)
    assert catalog.get_resource("target-1", "purchase_requests")["writable_columns"] == ["status"]


def test_semantic_command_compiles_to_resource_filters(monkeypatch):
    catalog = InMemoryDataCatalog()
    catalog.replace_snapshot("target-1", {"database": "db", "tables": [{"schema": "public", "table": "purchase_requests", "kind": "BASE TABLE", "primary_key": ["id"], "columns": [{"name": "id"}, {"name": "status"}, {"name": "department"}]}], "foreign_keys": []})
    catalog.upsert_semantic_command("target-1", "data.purchase.pending", "public.purchase_requests", "search", {"fixed_filters": {"status": "pending"}, "param_filters": {"department": "department"}, "limit": 100})
    executor = PostgresExecutor(catalog)
    captured = {}

    def fake_search(target, arguments):
        captured.update(arguments)
        return ExecutionResult(ok=True, result=arguments)

    monkeypatch.setattr(executor, "_resource_search", fake_search)
    capability = Capability("data.semantic.query.v1", "data semantic", "", KernelMode.DATA, "postgres", "semantic_query", Risk.LOW, ConfirmationMode.DIRECT, False)
    target = type("Target", (), {"id": "target-1", "config": {}})()
    result = executor.execute(capability, target, {"command": "data.purchase.pending", "params": {"department": "research"}})
    assert result.ok is True
    assert captured["resource"] == "public.purchase_requests"
    assert captured["filters"] == {"status": "pending", "department": "research"}


def test_data_target_policy_can_disable_raw_sql():
    config = validate_target_config(TargetKind.DATA, {"dsn_env": "ERP_DSN", "allowed_schemas": ["public", "erp"], "raw_sql_query": False, "raw_sql_exec": False, "max_rows": 200})
    assert config["allowed_schemas"] == ["public", "erp"]
    assert config["raw_sql_query"] is False
    assert config["raw_sql_exec"] is False
    assert config["max_rows"] == 200
