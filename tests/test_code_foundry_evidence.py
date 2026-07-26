from __future__ import annotations

import pytest

from lighthouse.code_foundry import (
    CodeAction,
    CodeActionKind,
    CodeEvidenceKind,
    CodeObservation,
    EvidenceLedger,
)


def action(identifier: str, kind: CodeActionKind) -> CodeAction:
    return CodeAction(
        id=identifier,
        kind=kind,
        mutates_workspace=kind is CodeActionKind.PATCH,
    )


def observation(identifier: str, action_value: CodeAction, *, ok: bool = True, **payload) -> CodeObservation:
    return CodeObservation(
        id=identifier,
        action_id=action_value.id,
        kind=action_value.kind,
        ok=ok,
        payload=payload,
    )


def test_patch_advances_generation_and_invalidates_old_proof_evidence():
    ledger = EvidenceLedger()
    first_patch = action("patch-1", CodeActionKind.PATCH)
    ledger.record(first_patch, observation("obs-patch-1", first_patch, changed_paths=["src/app.py"]))

    for kind in (CodeActionKind.DIFF, CodeActionKind.TEST, CodeActionKind.REVIEW):
        value = action(f"{kind.value}-1", kind)
        ledger.record(value, observation(f"obs-{kind.value}-1", value))

    assert ledger.workspace_generation == 1
    assert ledger.changed_paths == ("src/app.py",)
    assert ledger.current_evidence(CodeEvidenceKind.TEST)

    second_patch = action("patch-2", CodeActionKind.PATCH)
    ledger.record(second_patch, observation("obs-patch-2", second_patch, result={"path": "src/parser.py"}))

    assert ledger.workspace_generation == 2
    assert ledger.changed_paths == ("src/app.py", "src/parser.py")
    assert ledger.current_evidence(CodeEvidenceKind.PATCH)
    assert not ledger.current_evidence(CodeEvidenceKind.DIFF)
    assert not ledger.current_evidence(CodeEvidenceKind.TEST)
    assert not ledger.current_evidence(CodeEvidenceKind.REVIEW)


def test_failed_patch_does_not_make_the_tree_dirty():
    ledger = EvidenceLedger()
    patch = action("patch-1", CodeActionKind.PATCH)

    assert ledger.record(patch, observation("obs-patch-1", patch, ok=False)) is None
    assert ledger.workspace_generation == 0
    assert ledger.changed_paths == ()
    assert not ledger.has_successful_patch


def test_observation_must_match_the_action_that_produced_it():
    ledger = EvidenceLedger()
    patch = action("patch-1", CodeActionKind.PATCH)
    wrong = CodeObservation(
        id="obs-1",
        action_id="other-action",
        kind=CodeActionKind.PATCH,
        ok=True,
    )

    with pytest.raises(ValueError, match="reference its recorded action"):
        ledger.record(patch, wrong)
