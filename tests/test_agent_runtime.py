from __future__ import annotations

from lighthouse.agent import AgentRuntime
from lighthouse.agent_adapter import AgentRepositoryAdapter
from lighthouse.agent_store import InMemoryAgentStore
from lighthouse.capabilities import CapabilityRegistry
from lighthouse.kernel import OperationKernel
from lighthouse.models import (
    AgentRunStatus,
    ExecutionResult,
    KernelMode,
    OperationStatus,
    TargetKind,
)
from lighthouse.provider import AgentDecision
from lighthouse.repository import InMemoryRepository


class SequenceProvider:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.states = []

    def decide(self, *, system_prompt, state):
        self.states.append((system_prompt, state))
        value = self.decisions.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, capability, target, arguments):
        self.calls.append((capability.tool_name, arguments))
        if capability.tool_name == "system.project.context.v1":
            return ExecutionResult(
                ok=True,
                result={
                    "cwd": "/project",
                    "files": ["AGENTS.md", "app.py"],
                    "instructions": [
                        {"path": "AGENTS.md", "content": "Run tests.", "truncated": False}
                    ],
                },
            )
        return ExecutionResult(
            ok=True,
            result={"tool": capability.tool_name, "arguments": arguments},
        )


def runtime(decisions):
    repository = InMemoryRepository()
    system_target = repository.create_target(
        name="server",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": "/project"},
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=system_target.id,
    )
    executor = FakeExecutor()
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(),
        {"system": executor, "postgres": executor},
    )
    provider = SequenceProvider(decisions)
    return (
        AgentRuntime(
            AgentRepositoryAdapter(InMemoryAgentStore(), repository),
            kernel,
            provider,
        ),
        kernel,
        repository,
        workspace,
        executor,
        provider,
    )


def test_agent_observes_tool_receipt_then_finishes():
    agent, _kernel, _repository, workspace, executor, provider = runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.git.status.v1",
                arguments={},
                reason="inspect",
            ),
            AgentDecision(kind="final", message="Repository is clean.", reason="verified"),
        ]
    )
    result = agent.start(
        task="Inspect the repository",
        workspace_id=workspace.id,
        actor="adsin",
        mode=KernelMode.AUTO,
    )
    assert result["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    assert result["run"]["final_message"] == "Repository is clean."
    assert [name for name, _args in executor.calls] == [
        "system.project.context.v1",
        "system.git.status.v1",
    ]
    assert any(step["kind"] == "observation" for step in result["steps"])
    assert "system.git.status.v1" in provider.states[-1][0]


def test_agent_pauses_for_confirmation_and_resumes():
    agent, kernel, _repository, workspace, executor, _provider = runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.shell.exec.v1",
                arguments={"command": "echo fixed"},
                reason="apply",
            ),
            AgentDecision(kind="final", message="Fixed and verified.", reason="done"),
        ]
    )
    pending = agent.start(
        task="Fix it",
        workspace_id=workspace.id,
        actor="adsin",
        auto_confirm=False,
    )
    assert pending["run"]["status"] == AgentRunStatus.AWAITING_CONFIRMATION.value
    operation_id = pending["run"]["pending_operation_id"]
    assert pending["pending_operation"]["operation"]["status"] == OperationStatus.AWAITING_CONFIRMATION.value
    assert "system.shell.exec.v1" not in [name for name, _args in executor.calls]

    kernel.confirm(operation_id, actor="adsin")
    done = agent.advance(pending["run"]["id"])
    assert done["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    assert "system.shell.exec.v1" in [name for name, _args in executor.calls]


def test_auto_is_granted_at_first_permission_and_reused_for_compatible_operation():
    agent, kernel, _repository, workspace, executor, _provider = runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.test.run.v1",
                arguments={"command": "pytest -q tests/one"},
                reason="verify first batch",
            ),
            AgentDecision(
                kind="tool",
                capability="system.test.run.v1",
                arguments={"command": "pytest -q tests/two"},
                reason="verify second batch",
            ),
            AgentDecision(kind="final", message="Tests passed.", reason="receipts"),
        ]
    )
    pending = agent.start(
        task="Run tests",
        workspace_id=workspace.id,
        actor="adsin",
        auto_confirm=False,
    )
    assert pending["run"]["status"] == AgentRunStatus.AWAITING_CONFIRMATION.value
    scoped = agent.authorize_auto(pending["run"]["id"], actor="adsin")
    assert scoped["run"]["auto_confirm"] is True
    assert scoped["run"]["auto_scope"]["allowed_capabilities"] == ["system.test.run.v1"]
    kernel.confirm(pending["run"]["pending_operation_id"], actor="adsin")
    done = agent.advance(pending["run"]["id"])
    assert done["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    assert [name for name, _args in executor.calls].count("system.test.run.v1") == 2
    assert any(step["kind"] == "auto_confirmation" for step in done["steps"])


def test_provider_failure_after_successful_receipt_becomes_warning_not_failure():
    agent, _kernel, _repository, workspace, _executor, _provider = runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.git.status.v1",
                arguments={},
                reason="inspect",
            ),
            ConnectionError("Server disconnected without sending a response."),
        ]
    )
    result = agent.start(
        task="Inspect then explain",
        workspace_id=workspace.id,
        actor="adsin",
    )
    assert result["run"]["status"] == AgentRunStatus.COMPLETED_WITH_WARNING.value
    assert result["run"]["execution_status"] == "succeeded"
    assert result["run"]["response_status"] == "provider_failed"
    assert "successful Receipt remains authoritative" in result["run"]["final_message"]


def test_agent_can_request_and_receive_user_input():
    agent, _kernel, _repository, workspace, _executor, _provider = runtime(
        [
            AgentDecision(kind="ask", message="Which service?", reason="ambiguous"),
            AgentDecision(kind="final", message="Using warehouse-api.", reason="answered"),
        ]
    )
    waiting = agent.start(
        task="Restart the service",
        workspace_id=workspace.id,
        actor="adsin",
    )
    assert waiting["run"]["status"] == AgentRunStatus.WAITING_INPUT.value
    done = agent.provide_input(
        waiting["run"]["id"],
        actor="adsin",
        message="warehouse-api",
    )
    assert done["run"]["status"] == AgentRunStatus.SUCCEEDED.value
