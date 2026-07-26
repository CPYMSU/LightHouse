"""Durable-event contract for a CodeFoundry coding run."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CodeRunEvent:
    kind: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        kind = self.kind.strip()
        if not kind:
            raise ValueError("CodeFoundry event kind must not be empty")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "payload", dict(self.payload))


class CodeRunEventSink(Protocol):
    def emit(self, event: CodeRunEvent) -> Any: ...
