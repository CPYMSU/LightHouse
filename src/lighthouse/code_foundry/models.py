from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class CodeRunState(StrEnum):
    CREATED = "created"
    BRIEF_READY = "brief_ready"
    INSPECTING = "inspecting"
    EDITING = "editing"
    VALIDATING = "validating"
    REVIEWING = "reviewing"
    VERIFIED = "verified"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    UNVERIFIED = "unverified"


class CodeActionKind(StrEnum):
    SEARCH = "search"
    READ = "read"
    LIST = "list"
    STATUS = "status"
    PATCH = "patch"
    DIFF = "diff"
    TEST = "test"
    REVIEW = "review"


class CodeEvidenceKind(StrEnum):
    PATCH = "patch"
    DIFF = "diff"
    TEST = "test"
    REVIEW = "review"


class CodeResultStatus(StrEnum):
    VERIFIED = "verified"
    NEEDS_INPUT = "needs_input"
    FAILED = "failed"
    UNVERIFIED = "unverified"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class CodeAction:
    """A LightHouse-native action visible to the coding model."""

    id: str
    kind: CodeActionKind
    arguments: dict[str, Any] = field(default_factory=dict)
    mutates_workspace: bool = False

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("code action id must not be empty")
        if self.kind is CodeActionKind.PATCH and not self.mutates_workspace:
            raise ValueError("patch actions must declare workspace mutation")
        object.__setattr__(self, "arguments", dict(self.arguments))


@dataclass(frozen=True)
class CodeObservation:
    """Normalized result of one CodeAction execution."""

    id: str
    action_id: str
    kind: CodeActionKind
    ok: bool
    payload: dict[str, Any] = field(default_factory=dict)
    started_at: datetime = field(default_factory=_utc_now)
    completed_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("code observation id must not be empty")
        if not self.action_id.strip():
            raise ValueError("code observation action id must not be empty")
        if self.completed_at < self.started_at:
            raise ValueError("code observation cannot complete before it starts")
        object.__setattr__(self, "payload", dict(self.payload))


@dataclass(frozen=True)
class CodeEvidence:
    """A durable, generation-scoped fact used by the verification gate."""

    id: str
    kind: CodeEvidenceKind
    observation_ids: tuple[str, ...]
    digest: str
    summary: dict[str, Any]
    workspace_generation: int

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("code evidence id must not be empty")
        if not self.observation_ids:
            raise ValueError("code evidence must reference an observation")
        if self.workspace_generation < 1:
            raise ValueError("code evidence generation must be positive")
        object.__setattr__(self, "observation_ids", tuple(self.observation_ids))
        object.__setattr__(self, "summary", dict(self.summary))


@dataclass(frozen=True)
class CodeResult:
    status: CodeResultStatus
    summary: str
    changed_paths: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "changed_paths", tuple(self.changed_paths))
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "blockers", tuple(self.blockers))
