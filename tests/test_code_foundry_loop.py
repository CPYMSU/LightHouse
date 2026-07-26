from __future__ import annotations

import asyncio

from lighthouse.code_foundry import (
    CodeAction,
    CodeActionKind,
    CodeBriefCompiler,
    CodeFoundryLoop,
    CodeModelResponse,
    CodeObservation,
    CodeResponseKind,
    CodeResultStatus,
    CodeRuntime,
)


class SequenceModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.histories = []
        self.instructions = []

    def respond(self, *, instructions, brief, history, tools):
        self.histories.append(history)
        self.instructions.append(instructions)
        return self.responses.pop(0)


class FakeCodeExecutor:
    async def execute(self, action: CodeAction) -> CodeObservation:
        payload = {"changed_paths": ["src/parser.py"]} if action.kind is CodeActionKind.PATCH else {}
        return CodeObservation(
            id=f"observation-{action.id}",
            action_id=action.id,
            kind=action.kind,
            ok=True,
            payload=payload,
        )


def action(identifier: str, kind: CodeActionKind) -> CodeAction:
    return CodeAction(
        id=identifier,
        kind=kind,
        mutates_workspace=kind is CodeActionKind.PATCH,
    )


def brief():
    return CodeBriefCompiler().compile(task="Fix parser null handling.")


def test_loop_runs_actions_then_returns_a_verified_evidence_backed_result():
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
            CodeModelResponse(CodeResponseKind.FINAL, message="Parser guard implemented and validated."),
        ]
    )
    loop = CodeFoundryLoop(model=model, runtime=CodeRuntime(FakeCodeExecutor()))

    outcome = asyncio.run(loop.run(brief()))

    assert outcome.result.status is CodeResultStatus.VERIFIED
    assert outcome.result.changed_paths == ("src/parser.py",)
    assert outcome.turns == 3
    assert len(outcome.result.evidence_ids) == 4
    assert "<code_tools>" in model.instructions[0]
    assert "<code_tools>" not in model.instructions[1]


def test_loop_feeds_missing_proof_back_to_the_model_instead_of_accepting_a_claim():
    model = SequenceModel(
        [
            CodeModelResponse(CodeResponseKind.ACTIONS, actions=(action("patch", CodeActionKind.PATCH),)),
            CodeModelResponse(CodeResponseKind.FINAL, message="Done."),
            CodeModelResponse(
                CodeResponseKind.ACTIONS,
                actions=(
                    action("diff", CodeActionKind.DIFF),
                    action("test", CodeActionKind.TEST),
                    action("review", CodeActionKind.REVIEW),
                ),
            ),
            CodeModelResponse(CodeResponseKind.FINAL, message="Done with proof."),
        ]
    )
    loop = CodeFoundryLoop(model=model, runtime=CodeRuntime(FakeCodeExecutor()))

    outcome = asyncio.run(loop.run(brief()))

    assert outcome.result.status is CodeResultStatus.VERIFIED
    gate_messages = [
        item.payload["content"]
        for item in model.histories[2]
        if item.kind.value == "summary"
    ]
    assert gate_messages == [
        "Verification gate blocked completion: missing a post-patch diff; "
        "missing a successful post-patch validation; missing a post-patch review"
    ]


def test_loop_returns_needs_input_without_claiming_completion():
    model = SequenceModel(
        [CodeModelResponse(CodeResponseKind.ASK, message="Which parser API should preserve the legacy behaviour?")]
    )
    loop = CodeFoundryLoop(model=model, runtime=CodeRuntime(FakeCodeExecutor()))

    outcome = asyncio.run(loop.run(brief()))

    assert outcome.result.status is CodeResultStatus.NEEDS_INPUT
    assert outcome.result.summary.startswith("Which parser API")
