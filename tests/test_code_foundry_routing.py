from __future__ import annotations

from lighthouse.agent import AgentRuntime
from lighthouse.agent_adapter import AgentRepositoryAdapter
from lighthouse.agent_store import InMemoryAgentStore
from lighthouse.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from lighthouse.config import normalize_code_foundry_mode
from lighthouse.engineering import AdaptiveEngineeringMixin
from lighthouse.kernel import OperationKernel
from lighthouse.models import AgentRunStatus, ExecutionResult, TargetKind
from lighthouse.provider import AgentDecision
from lighthouse.repository import InMemoryRepository


class RoutedEngineeringRuntime(AdaptiveEngineeringMixin, AgentRuntime):
    pass


class SequenceProvider:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = []

    def decide(self, *, system_prompt, state):
        self.calls.append((system_prompt, state))
        return self.decisions.pop(0)


class CodeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, capability, _target, arguments):
        self.calls.append(capability.tool_name)
        if capability.tool_name == "system.project.context.v1":
            return ExecutionResult(
                ok=True,
                result={
                    "cwd": "/project",
                    "files": ["src/app.py", "tests/test_app.py"],
                    "instructions": [{"path": "AGENTS.md", "content": "Test the change.", "truncated": False}],
                },
            )
        if capability.tool_name == "system.git.diff.v1":
            return ExecutionResult(
                ok=True,
                result={
                    "diff": "diff --git a/src/app.py b/src/app.py\n"
                    "--- a/src/app.py\n"
                    "+++ b/src/app.py\n"
                    "@@ -1 +1 @@\n"
                    "-old\n"
                    "+new\n"
                },
            )
        return ExecutionResult(ok=True, result={"arguments": dict(arguments)})


def routed_runtime(decisions, *, mode: str):
    repository = InMemoryRepository()
    target = repository.create_target(
        name="project",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": "/project", "allowed_roots": ["/project"]},
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=target.id,
    )
    executor = CodeExecutor()
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(DEFAULT_CAPABILITIES),
        {"system": executor, "postgres": executor},
    )
    provider = SequenceProvider(decisions)
    runtime = RoutedEngineeringRuntime(
        AgentRepositoryAdapter(InMemoryAgentStore(), repository),
        kernel,
        provider,
    )
    runtime.code_foundry_mode = mode
    return runtime, workspace, executor, provider


def test_code_foundry_mode_parser_accepts_only_the_three_rollout_states():
    assert [normalize_code_foundry_mode(value) for value in ("off", "shadow", "on")] == [
        "off",
        "shadow",
        "on",
    ]
    try:
        normalize_code_foundry_mode("everywhere")
    except ValueError as exc:
        assert "off, shadow, or on" in str(exc)
    else:  # pragma: no cover - make an accidental permissive rollout visible
        raise AssertionError("invalid CodeFoundry mode was accepted")


def test_on_routes_a_coding_run_through_code_foundry_as_the_authoritative_loop():
    runtime, workspace, executor, provider = routed_runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.file.patch.v1",
                arguments={"patch": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"},
            ),
            AgentDecision(kind="tool", capability="system.git.diff.v1", arguments={}),
            AgentDecision(kind="tool", capability="system.test.run.v1", arguments={"command": "pytest -q"}),
            AgentDecision(kind="tool", capability="lighthouse.code_review.v1", arguments={}),
            AgentDecision(kind="final", message="Implemented and verified."),
        ],
        mode="on",
    )

    completed = runtime.start(
        task="Implement the parser fix and tests.",
        workspace_id=workspace.id,
        actor="operator",
    )

    assert completed["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    assert completed["run"]["response_status"] == "code_foundry_verified"
    assert not [step for step in completed["steps"] if step["kind"] == "decision"]
    route = next(step["payload"] for step in completed["steps"] if step["kind"] == "code_foundry.route_selected")
    assert route == {"mode": "on", "authoritative": True, "reason": "feature flag and coding-route classifier matched"}
    result = next(step["payload"] for step in completed["steps"] if step["kind"] == "code_foundry.route_completed")
    assert result["status"] == "verified"
    assert result["changed_paths"] == ["src/app.py"]
    assert executor.calls == [
        "system.project.context.v1",
        "system.file.patch.v1",
        "system.git.diff.v1",
        "system.test.run.v1",
        "system.git.diff.v1",
    ]
    assert len(provider.calls) == 5


def test_shadow_records_a_read_only_code_foundry_trace_and_keeps_legacy_result_authoritative():
    runtime, workspace, executor, _provider = routed_runtime(
        [
            AgentDecision(
                kind="tool",
                capability="system.file.patch.v1",
                arguments={"patch": "--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-old\n+new\n"},
            ),
            AgentDecision(kind="final", message="Shadow candidate complete."),
            AgentDecision(kind="final", message="Legacy result remains authoritative."),
        ],
        mode="shadow",
    )

    completed = runtime.start(
        task="Fix the parser implementation.",
        workspace_id=workspace.id,
        actor="operator",
    )

    assert completed["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    shadow = next(step["payload"] for step in completed["steps"] if step["kind"] == "code_foundry.shadow_completed")
    assert shadow["authoritative"] is False
    assert "withheld CodeFoundry workspace mutations" in shadow["note"]
    blocked = next(step["payload"] for step in completed["steps"] if step["kind"] == "code_foundry.observation_recorded")
    assert blocked["payload"]["shadow"] is True
    assert blocked["payload"]["blocked"] == "workspace mutation withheld in CodeFoundry shadow mode"
    assert "system.file.patch.v1" not in executor.calls
    assert any(step["kind"] == "decision" for step in completed["steps"])


def test_on_leaves_non_coding_requests_on_the_existing_engineering_loop():
    runtime, workspace, _executor, _provider = routed_runtime(
        [AgentDecision(kind="final", message="The status report is complete.")],
        mode="on",
    )

    completed = runtime.start(
        task="Summarize the current business status.",
        workspace_id=workspace.id,
        actor="operator",
    )

    assert completed["run"]["status"] == AgentRunStatus.SUCCEEDED.value
    skipped = next(step["payload"] for step in completed["steps"] if step["kind"] == "code_foundry.route_skipped")
    assert skipped["requested_mode"] == "on"
    assert "coding-route classifier" in skipped["reason"]
