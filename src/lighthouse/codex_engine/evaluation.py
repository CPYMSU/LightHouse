from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
from statistics import median
import time
from typing import Any, Protocol


@dataclass(frozen=True)
class EvaluationCase:
    id: str
    repository: str
    task: str
    acceptance: tuple[str, ...] = ()
    expected_evidence: tuple[str, ...] = ()
    validation_commands: tuple[tuple[str, ...], ...] = ()

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EvaluationCase":
        commands = tuple(tuple(str(part) for part in command) for command in value.get("validation_commands") or [])
        return cls(
            id=str(value["id"]),
            repository=str(value["repository"]),
            task=str(value["task"]),
            acceptance=tuple(str(item) for item in value.get("acceptance") or []),
            expected_evidence=tuple(str(item) for item in value.get("expected_evidence") or []),
            validation_commands=commands,
        )


@dataclass(frozen=True)
class EngineCaseResult:
    case_id: str
    engine: str
    status: str
    elapsed_ms: int
    changed_paths: tuple[str, ...] = ()
    diff_bytes: int = 0
    tests_passed: bool | None = None
    evidence: tuple[str, ...] = ()
    turns: int = 0
    tokens: int = 0
    receipt_digest: str = ""
    error: str | None = None

    @property
    def verified(self) -> bool:
        return self.status in {"verified", "completed", "succeeded"} and self.tests_passed is not False


class EvaluationAdapter(Protocol):
    name: str
    def run(self, case: EvaluationCase) -> EngineCaseResult: ...


@dataclass(frozen=True)
class EvaluationReport:
    generated_at: str
    results: tuple[EngineCaseResult, ...]
    summary: dict[str, dict[str, Any]]
    promotion: dict[str, Any]

    def public_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "results": [result.__dict__ | {"changed_paths": list(result.changed_paths), "evidence": list(result.evidence)} for result in self.results],
            "summary": self.summary,
            "promotion": self.promotion,
        }


class PromotionGate:
    """Fail-closed comparison before switching the default coding engine."""

    def __init__(self, *, max_diff_ratio: float = 1.25, max_token_ratio: float = 1.50):
        self.max_diff_ratio = max_diff_ratio
        self.max_token_ratio = max_token_ratio

    def decide(self, summary: dict[str, dict[str, Any]], *, baseline: str, candidate: str) -> dict[str, Any]:
        base = summary.get(baseline) or {}
        cand = summary.get(candidate) or {}
        reasons: list[str] = []
        if not base or not cand:
            reasons.append("baseline or candidate results are missing")
        if float(cand.get("verified_rate") or 0) < float(base.get("verified_rate") or 0):
            reasons.append("candidate verified completion rate is below baseline")
        if int(cand.get("test_failures") or 0) > int(base.get("test_failures") or 0):
            reasons.append("candidate has more validation failures")
        base_diff = max(1.0, float(base.get("median_diff_bytes") or 0))
        if float(cand.get("median_diff_bytes") or 0) > base_diff * self.max_diff_ratio:
            reasons.append("candidate produces materially wider diffs")
        base_tokens = max(1.0, float(base.get("median_tokens") or 0))
        if float(cand.get("median_tokens") or 0) > base_tokens * self.max_token_ratio:
            reasons.append("candidate token cost exceeds the configured ratio")
        if int(cand.get("missing_evidence") or 0) > 0:
            reasons.append("candidate has completed changes without required evidence")
        return {"promote": not reasons, "baseline": baseline, "candidate": candidate, "reasons": reasons}


class EvaluationRunner:
    def __init__(self, adapters: list[EvaluationAdapter], *, gate: PromotionGate | None = None):
        if len({adapter.name for adapter in adapters}) != len(adapters):
            raise ValueError("evaluation adapter names must be unique")
        self.adapters = tuple(adapters)
        self.gate = gate or PromotionGate()

    @staticmethod
    def load_cases(path: str | Path) -> list[EvaluationCase]:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        items = value.get("cases") if isinstance(value, dict) else value
        if not isinstance(items, list):
            raise ValueError("evaluation fixture must be an array or {cases: [...]} object")
        return [EvaluationCase.from_dict(dict(item)) for item in items]

    def run(self, cases: list[EvaluationCase], *, baseline: str = "native", candidate: str = "codex") -> EvaluationReport:
        results: list[EngineCaseResult] = []
        for case in cases:
            for adapter in self.adapters:
                started = time.monotonic()
                try:
                    result = adapter.run(case)
                except Exception as exc:
                    result = EngineCaseResult(
                        case_id=case.id,
                        engine=adapter.name,
                        status="failed",
                        elapsed_ms=round((time.monotonic() - started) * 1000),
                        error=f"{type(exc).__name__}: {exc}",
                    )
                results.append(result)
        summary = self._summarize(results, cases)
        promotion = self.gate.decide(summary, baseline=baseline, candidate=candidate)
        return EvaluationReport(
            generated_at=datetime.now(UTC).isoformat(),
            results=tuple(results),
            summary=summary,
            promotion=promotion,
        )

    @staticmethod
    def _summarize(results: list[EngineCaseResult], cases: list[EvaluationCase]) -> dict[str, dict[str, Any]]:
        expected_by_case = {case.id: set(case.expected_evidence) for case in cases}
        engines = sorted({result.engine for result in results})
        summary: dict[str, dict[str, Any]] = {}
        for engine in engines:
            items = [result for result in results if result.engine == engine]
            verified = [item for item in items if item.verified]
            missing = sum(
                1 for item in items
                if item.verified and not expected_by_case.get(item.case_id, set()).issubset(set(item.evidence))
            )
            summary[engine] = {
                "cases": len(items),
                "verified": len(verified),
                "verified_rate": len(verified) / len(items) if items else 0.0,
                "test_failures": sum(item.tests_passed is False for item in items),
                "missing_evidence": missing,
                "median_elapsed_ms": median([item.elapsed_ms for item in items]) if items else 0,
                "median_diff_bytes": median([item.diff_bytes for item in items]) if items else 0,
                "median_turns": median([item.turns for item in items]) if items else 0,
                "median_tokens": median([item.tokens for item in items]) if items else 0,
            }
        return summary
