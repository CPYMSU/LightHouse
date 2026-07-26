from __future__ import annotations

from lighthouse.code_foundry import (
    CodeAction,
    CodeActionKind,
    CodeObservation,
    CodeResultStatus,
    EvidenceLedger,
    VerificationGate,
)


def record(ledger: EvidenceLedger, identifier: str, kind: CodeActionKind, *, ok: bool = True) -> None:
    action = CodeAction(
        id=identifier,
        kind=kind,
        mutates_workspace=kind is CodeActionKind.PATCH,
    )
    ledger.record(
        action,
        CodeObservation(
            id=f"observation-{identifier}",
            action_id=identifier,
            kind=kind,
            ok=ok,
            payload={"changed_paths": ["src/app.py"]} if kind is CodeActionKind.PATCH else {},
        ),
    )


def verify_after_patch(ledger: EvidenceLedger) -> None:
    record(ledger, "diff", CodeActionKind.DIFF)
    record(ledger, "test", CodeActionKind.TEST)
    record(ledger, "review", CodeActionKind.REVIEW)


def test_changed_code_cannot_be_verified_without_current_proof():
    ledger = EvidenceLedger()
    record(ledger, "patch", CodeActionKind.PATCH)

    decision = VerificationGate().evaluate(ledger)

    assert decision.result.status is CodeResultStatus.UNVERIFIED
    assert decision.result.blockers == (
        "missing a post-patch diff",
        "missing a successful post-patch validation",
        "missing a post-patch review",
    )


def test_changed_code_is_verified_only_with_diff_validation_and_review():
    ledger = EvidenceLedger()
    record(ledger, "patch", CodeActionKind.PATCH)
    verify_after_patch(ledger)

    decision = VerificationGate().evaluate(ledger, summary="Implemented parser guard.")

    assert decision.result.status is CodeResultStatus.VERIFIED
    assert decision.result.summary == "Implemented parser guard."
    assert decision.result.changed_paths == ("src/app.py",)
    assert len(decision.result.evidence_ids) == 4


def test_old_validation_cannot_verify_a_later_patch():
    ledger = EvidenceLedger()
    record(ledger, "patch-1", CodeActionKind.PATCH)
    verify_after_patch(ledger)
    assert VerificationGate().evaluate(ledger).result.status is CodeResultStatus.VERIFIED

    record(ledger, "patch-2", CodeActionKind.PATCH)
    decision = VerificationGate().evaluate(ledger)

    assert decision.result.status is CodeResultStatus.UNVERIFIED
    assert "missing a post-patch diff" in decision.result.blockers


def test_latest_failed_validation_blocks_completion_until_a_successful_rerun():
    ledger = EvidenceLedger()
    record(ledger, "patch", CodeActionKind.PATCH)
    record(ledger, "diff", CodeActionKind.DIFF)
    record(ledger, "test-failed", CodeActionKind.TEST, ok=False)

    failed = VerificationGate().evaluate(ledger)
    assert failed.result.status is CodeResultStatus.FAILED
    assert failed.result.blockers == ("latest test action failed",)

    record(ledger, "test-passed", CodeActionKind.TEST)
    record(ledger, "review", CodeActionKind.REVIEW)
    assert VerificationGate().evaluate(ledger).result.status is CodeResultStatus.VERIFIED


def test_a_later_review_cannot_hide_a_failed_validation():
    ledger = EvidenceLedger()
    record(ledger, "patch", CodeActionKind.PATCH)
    record(ledger, "diff", CodeActionKind.DIFF)
    record(ledger, "test-failed", CodeActionKind.TEST, ok=False)
    record(ledger, "review", CodeActionKind.REVIEW)

    decision = VerificationGate().evaluate(ledger)

    assert decision.result.status is CodeResultStatus.FAILED
    assert decision.result.blockers == ("latest test action failed",)


def test_read_only_work_does_not_require_patch_evidence():
    ledger = EvidenceLedger()
    record(ledger, "read", CodeActionKind.READ)

    decision = VerificationGate().evaluate(ledger)

    assert decision.result.status is CodeResultStatus.VERIFIED
    assert decision.required_evidence == ()
