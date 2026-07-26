from __future__ import annotations

import asyncio

from lighthouse.agent_store import InMemoryAgentStore
from lighthouse.code_foundry import (
    CodeAction,
    CodeActionKind,
    CodeBriefCompiler,
    CodeFoundryLoop,
    CodeFoundryRunService,
    CodeModelResponse,
    CodeObservation,
    CodeResponseKind,
    CodeRuntime,
)
from lighthouse.models import KernelMode


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)

    def respond(self, **_kwargs):
        return self.responses.pop(0)


class Executor:
    async def execute(self, action):
        return CodeObservation(
            id=f"observation-{action.id}",
            action_id=action.id,
            kind=action.kind,
            ok=True,
            payload={"changed_paths": ["src/main.py"]} if action.kind is CodeActionKind.PATCH else {},
        )


def action(identifier, kind):
    return CodeAction(
        id=identifier,
        kind=kind,
        mutates_workspace=kind is CodeActionKind.PATCH,
    )


def test_durable_service_records_coding_lifecycle_and_terminal_evidence():
    store = InMemoryAgentStore()
    model = SequenceModel(
        [
            CodeModelResponse(CodeResponseKind.ACTIONS, actions=(action("patch", CodeActionKind.PATCH),)),
            CodeModelResponse(
                CodeResponseKind.ACTIONS,
                actions=(
                    action("diff", CodeActionKind.DIFF),
                    action("test", CodeActionKind.TEST),
                    action("review", CodeActionKind.REVIEW),
                ),
            ),
            CodeModelResponse(CodeResponseKind.FINAL, message="Verified."),
        ]
    )
    service = CodeFoundryRunService(
        store,
        loop_factory=lambda sink: CodeFoundryLoop(
            model=model,
            runtime=CodeRuntime(Executor()),
            event_sink=sink,
        ),
    )

    durable = asyncio.run(
        service.start_and_run(
            brief=CodeBriefCompiler().compile(task="Fix the parser."),
            workspace_id="workspace-1",
            actor="operator",
            mode=KernelMode.AUTO,
            run_id="code-run-1",
        )
    )

    assert durable.run["status"] == "succeeded"
    assert durable.run["response_status"] == "verified"
    events = store.list_steps("code-run-1")
    assert [event["kind"] for event in events] == [
        "code_foundry.run_created",
        "code_foundry.started",
        "code_foundry.turn_started",
        "code_foundry.model_response",
        "code_foundry.action_requested",
        "code_foundry.observation_recorded",
        "code_foundry.turn_started",
        "code_foundry.model_response",
        "code_foundry.action_requested",
        "code_foundry.action_requested",
        "code_foundry.action_requested",
        "code_foundry.observation_recorded",
        "code_foundry.observation_recorded",
        "code_foundry.observation_recorded",
        "code_foundry.turn_started",
        "code_foundry.model_response",
        "code_foundry.completed",
    ]
    completed = events[-1]["payload"]
    assert completed["status"] == "verified"
    assert completed["evidence_ids"] == ["evidence-1", "evidence-2", "evidence-3", "evidence-4"]
