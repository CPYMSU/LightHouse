from __future__ import annotations

from dataclasses import dataclass

from .evidence import EvidenceLedger
from .models import CodeActionKind, CodeEvidenceKind, CodeResult, CodeResultStatus


_REQUIRED_EVIDENCE = (
    CodeEvidenceKind.DIFF,
    CodeEvidenceKind.TEST,
    CodeEvidenceKind.REVIEW,
)
_REQUIRED_ACTIONS = (
    CodeActionKind.DIFF,
    CodeActionKind.TEST,
    CodeActionKind.REVIEW,
)
_MISSING_MESSAGES = {
    CodeEvidenceKind.DIFF: "missing a post-patch diff",
    CodeEvidenceKind.TEST: "missing a successful post-patch validation",
    CodeEvidenceKind.REVIEW: "missing a post-patch review",
}


@dataclass(frozen=True)
class VerificationDecision:
    result: CodeResult
    required_evidence: tuple[CodeEvidenceKind, ...]


class VerificationGate:
    """Computes completion from evidence rather than a model's final claim."""

    def evaluate(self, ledger: EvidenceLedger, *, summary: str = "") -> VerificationDecision:
        if not ledger.has_successful_patch:
            return VerificationDecision(
                result=CodeResult(
                    status=CodeResultStatus.VERIFIED,
                    summary=summary or "Read-only task completed without workspace changes.",
                ),
                required_evidence=(),
            )

        failed = ledger.latest_required_failure(_REQUIRED_ACTIONS)
        if failed is not None:
            return VerificationDecision(
                result=CodeResult(
                    status=CodeResultStatus.FAILED,
                    summary=summary or "Required validation failed.",
                    changed_paths=ledger.changed_paths,
                    evidence_ids=ledger.current_evidence_ids(),
                    blockers=(f"latest {failed.kind.value} action failed",),
                ),
                required_evidence=_REQUIRED_EVIDENCE,
            )

        missing = tuple(
            kind for kind in _REQUIRED_EVIDENCE if not ledger.current_evidence(kind)
        )
        if missing:
            return VerificationDecision(
                result=CodeResult(
                    status=CodeResultStatus.UNVERIFIED,
                    summary=summary or "Code change requires current verification evidence.",
                    changed_paths=ledger.changed_paths,
                    evidence_ids=ledger.current_evidence_ids(),
                    blockers=tuple(_MISSING_MESSAGES[kind] for kind in missing),
                ),
                required_evidence=_REQUIRED_EVIDENCE,
            )

        return VerificationDecision(
            result=CodeResult(
                status=CodeResultStatus.VERIFIED,
                summary=summary or "Code change verified by current diff, validation, and review evidence.",
                changed_paths=ledger.changed_paths,
                evidence_ids=ledger.current_evidence_ids(),
            ),
            required_evidence=_REQUIRED_EVIDENCE,
        )
