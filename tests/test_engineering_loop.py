from __future__ import annotations

import json

from lighthouse.agent import AgentRuntime
from lighthouse.agent_adapter import AgentRepositoryAdapter
from lighthouse.agent_store import InMemoryAgentStore
from lighthouse.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from lighthouse.engineering import AdaptiveEngineeringMixin, StructuredOpenAICompatibleProvider
from lighthouse.kernel import OperationKernel
from lighthouse.models import (
    AgentRunStatus,
    Capability,
    ConfirmationMode,
    ExecutionResult,
    KernelMode,
    Risk,
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
        return self.decisions.pop(0)


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, capability, target, arguments):
        self.calls.append((capability.tool_name, dict(arguments)))
        if capability.tool_name == "system.project.context.v1":
            return ExecutionResult(
                ok=True,
                result={
                    "cwd": "/project",
                    "files": ["AGENTS.md", "app.py"],
                    "instructions": [
                        {"path": "AGENTS.md", "content": "Improve existing code.", "truncated": False}
                    ],
                },
            )
        return ExecutionResult(
            ok=True,
            result={"tool": capability.tool_name, "arguments": dict(arguments)},
        )


class EngineeringRuntime(AdaptiveEngineeringMixin, AgentRuntime):
    pass


def runtime(decisions, *, capabilities=DEFAULT_CAPABILITIES):
    repository = InMemoryRepository()
    system_target = repository.create_target(
        name="server",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": "/project", "allowed_roots": ["/project"]},
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=system_target.id,
    )
    executor = FakeExecutor()
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(capabilities),
        {"system": executor, "postgres": executor},
    )
    provider = SequenceProvider(decisions)
    agent = EngineeringRuntime(
        AgentRepositoryAdapter(InMemoryAgentStore(), repository),
        kernel,
        provider,
    )
    return agent, kernel, workspace, executor, provider


def test_one_auto_confirmation_covers_all_later_run_capabilities():
    passkey_capability = Capability(
        tool_name="system.passkey.fixture.v1",
        command="passkey fixture",
        description="Test a passkey-governed operation",
        kernel=KernelMode.SYSTEM,
        executor="system",
        operation="fixture",
        risk=Risk.CRITICAL,
        confirmation=ConfirmationMode.PASSKEY,
        writes=True,
    )
    agent, _kernel, workspace, executor, _provider = runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.shell.exec.v1",
                arguments={"command": "echo first"},
                reason="first governed action",
            ),
            AgentDecision(
                kind="tool",
                capability="system.test.run.v1",
                arguments={"command": "pytest -q"},
                reason="verify",
            ),
            AgentDecision(
                kind="tool",
                capability="system.passkey.fixture.v1",
                arguments={},
                reason="critical final operation",
            ),
            AgentDecision(kind="final", message="Completed with receipts.", reason="verified"),
        ],
        capabilities=(*DEFAULT_CAPABILITIES, passkey_capability),
    )

    pending = agent.start(
        task="Apply and verify the change",
        workspace_id=workspace.id,
        actor="adsin",
        max_steps=3,
    )
    assert pending["run"]["status"] == AgentRunStatus.AWAITING_CONFIRMATION.value

    done = agent.authorize_auto(pending["run"]["id"], actor="adsin")

    assert done["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    assert [name for name, _arguments in executor.calls] == [
        "system.project.context.v1",
        "system.shell.exec.v1",
        "system.test.run.v1",
        "system.passkey.fixture.v1",
    ]
    scopes = [step for step in done["steps"] if step["kind"] == "auto_scope_granted"]
    confirmations = [step for step in done["steps"] if step["kind"] == "auto_confirmation"]
    assert len(scopes) == 1
    assert len(confirmations) == 3
    assert scopes[0]["payload"]["scope"]["run_wide"] is True
    assert scopes[0]["payload"]["scope"]["allowed_capabilities"] == ["*"]


def test_code_final_candidate_is_revised_until_diff_and_validation_exist():
    agent, kernel, workspace, _executor, _provider = runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.file.patch.v1",
                arguments={
                    "patch": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
                },
                reason="patch active implementation",
            ),
            AgentDecision(kind="final", message="Changed.", reason="initial candidate"),
            AgentDecision(
                kind="tool",
                capability="system.git.diff.v1",
                arguments={},
                reason="review diff",
            ),
            AgentDecision(
                kind="tool",
                capability="system.file.read.v1",
                arguments={"path": "app.py"},
                reason="validate resulting file",
            ),
            AgentDecision(kind="final", message="Changed and verified.", reason="evidence complete"),
        ]
    )

    pending = agent.start(
        task="Improve the existing app implementation",
        workspace_id=workspace.id,
        actor="adsin",
    )
    kernel.confirm(pending["run"]["pending_operation_id"], actor="adsin")
    done = agent.advance(pending["run"]["id"])

    assert done["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    reviews = [step["payload"] for step in done["steps"] if step["kind"] == "completion_review"]
    assert reviews[0]["status"] == "revise"
    assert "inspect the resulting Git diff" in " ".join(reviews[0]["guidance"])
    assert reviews[-1]["status"] == "pass"


def test_structured_provider_compacts_sections_into_valid_json():
    provider = StructuredOpenAICompatibleProvider(
        base_url="https://provider.example/v1",
        api_key="test-key",
        model="test-model",
        max_state_chars=10_000,
    )
    value = {
        "run": {"id": "run-1", "task": "Improve existing code"},
        "workspace": {"id": "workspace-1"},
        "engineering": {"operating_principle": "existing code first"},
        "context_intelligence": {
            "active_task": "Improve existing code",
            "verified_facts": ["fact"],
            "irrelevant_bulk": "x" * 80_000,
        },
        "steps": [
            {"sequence": index, "kind": "observation", "payload": {"text": "y" * 1000}}
            for index in range(100)
        ],
    }

    compacted = json.loads(provider._bounded_json(value))

    assert compacted["context_compacted"] is True
    assert compacted["run"]["id"] == "run-1"
    assert compacted["engineering"]["operating_principle"] == "existing code first"
    assert "head" not in compacted
    assert "tail" not in compacted


def test_model_can_expand_memory_before_asking_or_acting():
    agent, _kernel, workspace, _executor, provider = runtime(
        [
            AgentDecision(
                kind="memory_expand",
                arguments={"depth": "focused"},
                reason="the compact memory index lacks an earlier decision",
            ),
            AgentDecision(kind="final", message="Completed after recalling the prior decision.", reason="enough context"),
        ]
    )

    completed = agent.start(
        task="Continue the implementation from earlier work.",
        workspace_id=workspace.id,
        actor="adsin",
    )

    assert completed["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    expanded = [step["payload"] for step in completed["steps"] if step["kind"] == "memory_context_expanded"]
    assert expanded == [
        {
            "step": 1,
            "depth": "focused",
            "reason": "the compact memory index lacks an earlier decision",
            "source": "model_requested_progressive_memory",
        }
    ]
    assert "memory_expand" in provider.states[0][0]
