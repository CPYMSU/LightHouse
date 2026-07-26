import pytest

from lighthouse.code_foundry import (
    AgentProviderCodeAdapter,
    CodeBriefCompiler,
    CodeResponseKind,
)
from lighthouse.provider import AgentDecision, AgentProtocolError


class FakeProvider:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def decide(self, *, system_prompt, state):
        self.calls.append((system_prompt, state))
        return self.decision

    def distill(self, *, kind, payload):
        return {}


def test_adapter_converts_a_native_provider_tool_decision_to_a_typed_code_action():
    provider = FakeProvider(
        AgentDecision(
            kind="tool",
            capability="system.file.read.v1",
            arguments={"path": "src/main.py"},
        )
    )
    adapter = AgentProviderCodeAdapter(provider)

    response = adapter.respond(
        instructions="Inspect first.",
        brief=CodeBriefCompiler().compile(task="Read the entry point."),
        history=(),
        tools=adapter.registry.visible_specs(),
    )

    assert response.kind is CodeResponseKind.ACTIONS
    assert response.actions[0].kind.value == "read"
    assert response.actions[0].arguments == {"path": "src/main.py"}
    prompt, state = provider.calls[0]
    assert "one native LightHouse JSON decision" in prompt
    assert state["code_foundry"]["tools"][1]["arguments"]["path"]["required"] is True


def test_adapter_maps_final_and_rejects_out_of_surface_capabilities():
    final = AgentProviderCodeAdapter(FakeProvider(AgentDecision(kind="final", message="Done.")))
    response = final.respond(
        instructions="Inspect first.",
        brief=CodeBriefCompiler().compile(task="Read the entry point."),
        history=(),
        tools=final.registry.visible_specs(),
    )
    assert response.kind is CodeResponseKind.FINAL

    invalid = AgentProviderCodeAdapter(
        FakeProvider(AgentDecision(kind="tool", capability="system.shell.exec.v1", arguments={"command": "pwd"}))
    )
    with pytest.raises(AgentProtocolError, match="outside the CodeFoundry tool surface"):
        invalid.respond(
            instructions="Inspect first.",
            brief=CodeBriefCompiler().compile(task="Read the entry point."),
            history=(),
            tools=invalid.registry.visible_specs(),
        )


def test_adapter_rejects_missing_or_unexpected_arguments_before_execution():
    missing = AgentProviderCodeAdapter(
        FakeProvider(AgentDecision(kind="tool", capability="system.file.read.v1", arguments={}))
    )
    with pytest.raises(AgentProtocolError, match="missing required arguments"):
        missing.respond(
            instructions="Inspect first.",
            brief=CodeBriefCompiler().compile(task="Read the entry point."),
            history=(),
            tools=missing.registry.visible_specs(),
        )
