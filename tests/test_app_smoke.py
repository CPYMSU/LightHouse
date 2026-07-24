from lighthouse.agent import AgentRuntime
from lighthouse.agent_adapter import AgentRepositoryAdapter
from lighthouse.agent_store import InMemoryAgentStore
from lighthouse.api import create_app
from lighthouse.capabilities import CapabilityRegistry
from lighthouse.config import Settings
from lighthouse.executors import SystemExecutor
from lighthouse.kernel import OperationKernel
from lighthouse.models import ExecutionResult, TargetKind
from lighthouse.provider import AgentDecision
from lighthouse.repository import InMemoryRepository


class FinalProvider:
    def decide(self, *, system_prompt, state):
        return AgentDecision(kind="final", message="done", reason="test")


class FakeExecutor:
    def execute(self, capability, target, arguments):
        return ExecutionResult(ok=True, result={"ok": True})


def test_application_imports_and_agent_routes_exist():
    assert SystemExecutor is not None
    repository = InMemoryRepository()
    system = repository.create_target(
        name="local",
        kind=TargetKind.SYSTEM,
        config={"transport": "local"},
    )
    repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=system.id,
    )
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(),
        {"system": FakeExecutor(), "postgres": FakeExecutor()},
    )
    runtime = AgentRuntime(
        AgentRepositoryAdapter(InMemoryAgentStore(), repository),
        kernel,
        FinalProvider(),
    )
    app = create_app(
        Settings(database_url="postgresql://unused", api_key="x" * 32),
        kernel=kernel,
        agent_runtime=runtime,
    )
    paths = {route.path for route in app.routes}
    assert "/healthz" in paths
    assert "/v1/agent/runs" in paths
    assert "/v1/agent/runs/{run_id}/advance" in paths
