from __future__ import annotations

from io import StringIO

from rich.console import Console

from lighthouse.agent import AgentRuntime
from lighthouse.agent_adapter import AgentRepositoryAdapter
from lighthouse.agent_store import InMemoryAgentStore
from lighthouse.capabilities import CapabilityRegistry
from lighthouse.cognitive import (
    CognitiveAgentDecision,
    CognitiveContinuityMixin,
    build_cognitive_observer,
    parse_cognitive_decision,
)
from lighthouse.kernel import OperationKernel
from lighthouse.models import AgentRunStatus, ExecutionResult, KernelMode, TargetKind
from lighthouse.repository import InMemoryRepository
from lighthouse.ui_v12 import ObservatoryTerminal


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
                result={"cwd": "/project", "files": ["app.py"], "instructions": []},
            )
        return ExecutionResult(ok=True, result={"capability": capability.tool_name})


class CognitiveRuntime(CognitiveContinuityMixin, AgentRuntime):
    pass


def runtime(decisions):
    repository = InMemoryRepository()
    system_target = repository.create_target(
        name="system",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": "/project"},
    )
    workspace = repository.create_workspace(
        name="workspace",
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
    agent = CognitiveRuntime(
        AgentRepositoryAdapter(InMemoryAgentStore(), repository),
        kernel,
        provider,
    )
    return agent, workspace, executor, provider


def test_cognitive_decision_is_safe_and_preserves_structured_delta():
    decision = parse_cognitive_decision(
        {
            "kind": "tool",
            "capability": "system.file.read.v1",
            "arguments": {"path": "app.py"},
            "reason": "inspect active implementation",
            "display": {
                "phase": "investigating",
                "title": "Inspect current entry point",
                "summary": "Authorization: Bearer super-secret-token",
                "evidence": [{"path": "app.py", "symbol": "main"}],
            },
            "cognitive_delta": {
                "strategy": {"current": "modify the active implementation"},
                "next_intent": "review the current call path",
                "verified_facts": [{"claim": "API_KEY=hidden-value"}],
            },
        }
    )

    assert decision.display["phase"] == "investigating"
    assert "super-secret-token" not in decision.display["summary"]
    assert decision.cognitive_delta["strategy"]["current"] == "modify the active implementation"
    assert "hidden-value" not in str(decision.cognitive_delta)
    assert decision.public_dict()["display"]["title"] == "Inspect current entry point"


def test_cognitive_state_is_visible_and_becomes_next_turn_context():
    agent, workspace, executor, provider = runtime(
        [
            CognitiveAgentDecision(
                kind="tool",
                capability="system.git.status.v1",
                arguments={},
                reason="inspect repository",
                display={
                    "phase": "understanding",
                    "title": "Understand the current repository",
                    "summary": "I will improve the active implementation instead of replacing it.",
                    "details": [],
                    "evidence": [],
                    "importance": "important",
                    "visibility": "balanced",
                },
                cognitive_delta={
                    "understanding": {"current": "Improve the active implementation"},
                    "strategy": {"current": "existing code first"},
                    "next_intent": "inspect the current diff",
                },
            ),
            CognitiveAgentDecision(
                kind="final",
                message="Repository inspected.",
                reason="receipt available",
                display={
                    "phase": "completing",
                    "title": "Inspection complete",
                    "summary": "The repository status was verified.",
                    "details": [],
                    "evidence": [],
                    "importance": "important",
                    "visibility": "balanced",
                },
            ),
        ]
    )

    result = agent.start(
        task="Improve the existing code",
        workspace_id=workspace.id,
        actor="adsin",
        mode=KernelMode.AUTO,
    )

    assert result["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    observer = result["cognitive_observer"]
    assert observer["state"]["strategy"]["current"] == "existing code first"
    assert any(item["title"] == "Understand the current repository" for item in observer["timeline"])
    assert "cognitive_continuity" in provider.states[-1][1]
    assert provider.states[-1][1]["cognitive_continuity"]["state"]["next_intent"] == "inspect the current diff"
    assert [name for name, _arguments in executor.calls] == [
        "system.project.context.v1",
        "system.git.status.v1",
    ]


def test_user_direction_is_durable_and_has_context_priority():
    agent, workspace, _executor, _provider = runtime(
        [
            CognitiveAgentDecision(
                kind="tool",
                capability="system.shell.exec.v1",
                arguments={"command": "echo pending"},
                reason="governed action",
            )
        ]
    )
    pending = agent.start(
        task="Change the implementation",
        workspace_id=workspace.id,
        actor="adsin",
    )
    assert pending["run"]["status"] == AgentRunStatus.AWAITING_CONFIRMATION.value

    steered = agent.provide_direction(
        pending["run"]["id"],
        actor="adsin",
        message="Do not create a second service; reuse the current Run Steps.",
    )

    assert steered["cognitive_observer"]["state"]["user_directions"] == [
        "Do not create a second service; reuse the current Run Steps."
    ]
    assert any(step["kind"] == "user_direction" for step in steered["steps"])


def test_observer_derives_changed_files_validation_and_failure_timeline():
    observer = build_cognitive_observer(
        {"task": "Patch and test"},
        [
            {"sequence": 1, "kind": "run_created", "payload": {"task": "Patch and test"}},
            {
                "sequence": 2,
                "kind": "decision",
                "payload": {
                    "kind": "tool",
                    "capability": "system.file.patch.v1",
                    "arguments": {
                        "patch": "--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n"
                    },
                },
            },
            {
                "sequence": 3,
                "kind": "decision",
                "payload": {
                    "kind": "tool",
                    "capability": "system.test.run.v1",
                    "arguments": {"command": "pytest -q"},
                },
            },
            {
                "sequence": 4,
                "kind": "observation",
                "payload": {
                    "capability": "system.test.run.v1",
                    "receipt": {"ok": False, "result": {"error": "one test failed"}},
                },
            },
        ],
    )

    assert observer["state"]["active_work"]["changed_files"] == ["app.py"]
    assert observer["state"]["validation"]["failed"] == 1
    assert observer["state"]["active_work"]["stage"] == "recovering"
    assert any(item["phase"] == "recovering" for item in observer["timeline"])


def test_balanced_terminal_renders_cognition_without_private_raw_payloads():
    buffer = StringIO()
    ui = ObservatoryTerminal(
        console=Console(file=buffer, force_terminal=False, width=120),
        observe_mode="balanced",
    )
    snapshot = {
        "run": {"id": "run-1", "status": "running", "auto_confirm": True},
        "steps": [
            {
                "sequence": 1,
                "kind": "run_created",
                "payload": {"task": "Improve existing code"},
                "created_at": "now",
            },
            {
                "sequence": 2,
                "kind": "decision",
                "payload": {
                    "kind": "tool",
                    "capability": "system.file.patch.v1",
                    "arguments": {"patch": "+++ b/app.py\n"},
                    "display": {
                        "phase": "implementing",
                        "title": "Patch the active implementation",
                        "summary": "Reuse app.py and preserve public behavior.",
                        "details": [],
                        "evidence": [{"path": "app.py"}],
                        "importance": "important",
                        "visibility": "balanced",
                    },
                },
                "created_at": "later",
            },
        ],
        "cognitive_observer": build_cognitive_observer(
            {"task": "Improve existing code"},
            [
                {"sequence": 1, "kind": "run_created", "payload": {"task": "Improve existing code"}},
                {
                    "sequence": 2,
                    "kind": "decision",
                    "payload": {
                        "kind": "tool",
                        "capability": "system.file.patch.v1",
                        "arguments": {"patch": "+++ b/app.py\n"},
                        "display": {
                            "phase": "implementing",
                            "title": "Patch the active implementation",
                            "summary": "Reuse app.py and preserve public behavior.",
                            "importance": "important",
                            "visibility": "balanced",
                        },
                    },
                },
            ],
        ),
        "agent_observatory": {"active": 0, "total": 0, "items": []},
    }

    ui.render_run(snapshot)
    output = buffer.getvalue()

    assert "IMPLEMENTING" in output
    assert "Patch the active implementation" in output
    assert "EDIT" in output
    assert "app.py" in output
    assert "cognitive_delta" not in output
