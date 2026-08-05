from __future__ import annotations

from lighthouse.codex_engine.evaluation import (
    EngineCaseResult,
    EvaluationCase,
    EvaluationRunner,
    PromotionGate,
)


class Adapter:
    def __init__(self, name: str, *, verified: bool, diff: int, tokens: int = 100):
        self.name = name
        self.verified = verified
        self.diff = diff
        self.tokens = tokens

    def run(self, case: EvaluationCase) -> EngineCaseResult:
        return EngineCaseResult(
            case_id=case.id,
            engine=self.name,
            status="verified" if self.verified else "failed",
            elapsed_ms=10,
            diff_bytes=self.diff,
            tests_passed=self.verified,
            evidence=("patch", "diff", "test") if self.verified else (),
            tokens=self.tokens,
        )


def test_promotion_gate_accepts_equal_or_better_candidate() -> None:
    case = EvaluationCase(
        id="fix",
        repository=".",
        task="fix",
        expected_evidence=("patch", "diff", "test"),
    )
    report = EvaluationRunner([Adapter("native", verified=True, diff=100), Adapter("codex", verified=True, diff=110)]).run([case])
    assert report.promotion["promote"] is True


def test_promotion_gate_rejects_lower_completion() -> None:
    summary = {
        "native": {"verified_rate": 1, "test_failures": 0, "median_diff_bytes": 10, "median_tokens": 10, "missing_evidence": 0},
        "codex": {"verified_rate": 0, "test_failures": 1, "median_diff_bytes": 10, "median_tokens": 10, "missing_evidence": 0},
    }
    decision = PromotionGate().decide(summary, baseline="native", candidate="codex")
    assert decision["promote"] is False
