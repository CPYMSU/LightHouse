from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from .models import CodeAction, CodeActionKind, CodeEvidence, CodeEvidenceKind, CodeObservation


_EVIDENCE_BY_ACTION = {
    CodeActionKind.PATCH: CodeEvidenceKind.PATCH,
    CodeActionKind.DIFF: CodeEvidenceKind.DIFF,
    CodeActionKind.TEST: CodeEvidenceKind.TEST,
    CodeActionKind.REVIEW: CodeEvidenceKind.REVIEW,
}


class EvidenceLedger:
    """Tracks fresh engineering evidence across workspace mutations.

    Every successful patch advances the workspace generation. Diff, test, and
    review evidence from an earlier generation remains durable for audit, but
    can no longer satisfy verification for the current tree.
    """

    def __init__(self) -> None:
        self._actions: dict[str, CodeAction] = {}
        self._observations: list[tuple[int, CodeObservation]] = []
        self._evidence: list[CodeEvidence] = []
        self._workspace_generation = 0
        self._changed_paths: set[str] = set()

    @property
    def workspace_generation(self) -> int:
        return self._workspace_generation

    @property
    def changed_paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._changed_paths))

    @property
    def has_successful_patch(self) -> bool:
        return self._workspace_generation > 0

    def record(self, action: CodeAction, observation: CodeObservation) -> CodeEvidence | None:
        """Record one action result and create evidence for successful proof actions."""
        if action.id in self._actions:
            raise ValueError(f"duplicate code action id: {action.id}")
        if observation.action_id != action.id:
            raise ValueError("code observation must reference its recorded action")
        if observation.kind is not action.kind:
            raise ValueError("code observation kind must match its action kind")

        self._actions[action.id] = action
        if action.kind is CodeActionKind.PATCH and observation.ok:
            self._workspace_generation += 1
            self._changed_paths.update(_changed_paths(observation.payload))
        self._observations.append((self._workspace_generation, observation))

        evidence_kind = _EVIDENCE_BY_ACTION.get(action.kind)
        if evidence_kind is None or not observation.ok:
            return None
        if self._workspace_generation < 1:
            return None

        evidence = CodeEvidence(
            id=f"evidence-{len(self._evidence) + 1}",
            kind=evidence_kind,
            observation_ids=(observation.id,),
            digest=_digest_observation(observation),
            summary=_summary(action, observation),
            workspace_generation=self._workspace_generation,
        )
        self._evidence.append(evidence)
        return evidence

    def current_evidence(self, kind: CodeEvidenceKind) -> tuple[CodeEvidence, ...]:
        return tuple(
            evidence
            for evidence in self._evidence
            if evidence.kind is kind and evidence.workspace_generation == self._workspace_generation
        )

    def current_evidence_ids(self) -> tuple[str, ...]:
        return tuple(evidence.id for evidence in self._evidence if evidence.workspace_generation == self._workspace_generation)

    def latest_observation(self, kind: CodeActionKind, *, current_only: bool = True) -> CodeObservation | None:
        for generation, observation in reversed(self._observations):
            if observation.kind is not kind:
                continue
            if current_only and generation != self._workspace_generation:
                continue
            return observation
        return None

    def latest_required_failure(self, kinds: Iterable[CodeActionKind]) -> CodeObservation | None:
        values = set(kinds)
        latest_by_kind: dict[CodeActionKind, CodeObservation] = {}
        for generation, observation in reversed(self._observations):
            if (
                generation != self._workspace_generation
                or observation.kind not in values
                or observation.kind in latest_by_kind
            ):
                continue
            latest_by_kind[observation.kind] = observation
        for kind in values:
            observation = latest_by_kind.get(kind)
            if observation is not None and not observation.ok:
                return observation
        return None


def _changed_paths(payload: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for candidate in _path_candidates(payload):
        if isinstance(candidate, str) and candidate.strip():
            values.add(candidate)
    return values


def _path_candidates(payload: dict[str, Any]) -> Iterable[Any]:
    for key in ("changed_paths", "paths"):
        value = payload.get(key)
        if isinstance(value, (list, tuple, set)):
            yield from value
        elif value is not None:
            yield value
    for key in ("path", "relative_path"):
        value = payload.get(key)
        if value is not None:
            yield value
    result = payload.get("result")
    if isinstance(result, dict):
        yield from _path_candidates(result)


def _digest_observation(observation: CodeObservation) -> str:
    value = {
        "action_id": observation.action_id,
        "kind": observation.kind.value,
        "ok": observation.ok,
        "payload": observation.payload,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _summary(action: CodeAction, observation: CodeObservation) -> dict[str, Any]:
    return {
        "action_id": action.id,
        "action_kind": action.kind.value,
        "ok": observation.ok,
        "changed_paths": sorted(_changed_paths(observation.payload)),
    }
