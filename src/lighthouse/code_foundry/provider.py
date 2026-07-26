from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from .brief import CodeBrief
from .history import CodeHistoryItem
from .models import CodeAction
from .tools import CodeToolSpec


class CodeResponseKind(StrEnum):
    ACTIONS = "actions"
    ASK = "ask"
    FINAL = "final"


@dataclass(frozen=True)
class CodeModelResponse:
    """Provider-neutral model output for one CodeFoundry turn."""

    kind: CodeResponseKind
    actions: tuple[CodeAction, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "actions", tuple(self.actions))
        message = self.message.strip()
        object.__setattr__(self, "message", message)
        if self.kind is CodeResponseKind.ACTIONS and not self.actions:
            raise ValueError("action responses must include at least one action")
        if self.kind is not CodeResponseKind.ACTIONS and self.actions:
            raise ValueError("only action responses may include actions")
        if self.kind in {CodeResponseKind.ASK, CodeResponseKind.FINAL} and not message:
            raise ValueError("ask and final responses require a message")


class CodeModelAdapter(Protocol):
    def respond(
        self,
        *,
        instructions: str,
        brief: CodeBrief,
        history: tuple[CodeHistoryItem, ...],
        tools: tuple[CodeToolSpec, ...],
    ) -> CodeModelResponse | Any: ...
