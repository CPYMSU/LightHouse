from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from .models import CodeAction, CodeObservation
from .truncation import formatted_truncate_text


class CodeHistoryItemKind(StrEnum):
    BRIEF = "brief"
    ACTION = "action"
    OBSERVATION = "observation"
    SUMMARY = "summary"


@dataclass(frozen=True)
class CodeHistoryItem:
    sequence: int
    kind: CodeHistoryItemKind
    payload: dict[str, Any]
    paths: tuple[str, ...] = ()
    pinned: bool = False
    stale: bool = False

    def public_dict(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "kind": self.kind.value,
            "payload": dict(self.payload),
            "paths": list(self.paths),
            "pinned": self.pinned,
            "stale": self.stale,
        }


class CodeHistory:
    """Maintains a compact, invalidation-aware transcript for code turns."""

    def __init__(self) -> None:
        self._items: list[CodeHistoryItem] = []

    def add_brief(self, payload: dict[str, Any]) -> CodeHistoryItem:
        return self._append(CodeHistoryItemKind.BRIEF, payload, pinned=True)

    def add_action(self, action: CodeAction) -> CodeHistoryItem:
        return self._append(
            CodeHistoryItemKind.ACTION,
            {
                "id": action.id,
                "kind": action.kind.value,
                "arguments": dict(action.arguments),
                "mutates_workspace": action.mutates_workspace,
            },
            paths=_paths(action.arguments),
        )

    def add_observation(self, observation: CodeObservation, *, pinned: bool = False) -> CodeHistoryItem:
        return self._append(
            CodeHistoryItemKind.OBSERVATION,
            {
                "id": observation.id,
                "action_id": observation.action_id,
                "kind": observation.kind.value,
                "ok": observation.ok,
                "payload": dict(observation.payload),
            },
            paths=_paths(observation.payload),
            pinned=pinned,
        )

    def add_summary(self, content: str) -> CodeHistoryItem:
        text = str(content or "").strip()
        if not text:
            raise ValueError("code history summary must not be empty")
        return self._append(CodeHistoryItemKind.SUMMARY, {"content": text}, pinned=True)

    def invalidate_paths(self, changed_paths: tuple[str, ...] | list[str]) -> None:
        changed = {path for path in changed_paths if isinstance(path, str) and path}
        if not changed:
            return
        invalidated: list[CodeHistoryItem] = []
        for item in self._items:
            must_invalidate = (
                not item.pinned
                and not item.stale
                and item.kind is CodeHistoryItemKind.OBSERVATION
                and bool(set(item.paths) & changed)
            )
            invalidated.append(replace(item, stale=True) if must_invalidate else item)
        self._items = invalidated

    def compact(self, *, max_items: int) -> tuple[CodeHistoryItem, ...]:
        limit = max(1, int(max_items))
        pinned = [item for item in self._items if item.pinned]
        active = [item for item in self._items if not item.pinned and not item.stale]
        remaining = max(0, limit - len(pinned))
        selected = [*pinned, *active[-remaining:]] if remaining else pinned
        return tuple(sorted(selected, key=lambda item: item.sequence))

    def for_model(
        self,
        *,
        max_items: int,
        max_observation_bytes: int,
    ) -> tuple[CodeHistoryItem, ...]:
        """Return compact history with large tool output bounded for model context.

        The canonical in-memory transcript remains unchanged for audit and
        recovery.  Only untrusted observation payload strings are shortened.
        """

        return tuple(
            replace(item, payload=_truncate_observation_payload(item.payload, max_observation_bytes))
            if item.kind is CodeHistoryItemKind.OBSERVATION
            else item
            for item in self.compact(max_items=max_items)
        )

    def items(self, *, include_stale: bool = False) -> tuple[CodeHistoryItem, ...]:
        return tuple(item for item in self._items if include_stale or not item.stale)

    def _append(
        self,
        kind: CodeHistoryItemKind,
        payload: dict[str, Any],
        *,
        paths: tuple[str, ...] = (),
        pinned: bool = False,
    ) -> CodeHistoryItem:
        item = CodeHistoryItem(
            sequence=len(self._items) + 1,
            kind=kind,
            payload=dict(payload),
            paths=tuple(paths),
            pinned=pinned,
        )
        self._items.append(item)
        return item


def _paths(payload: dict[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    for key in ("path", "relative_path", "file"):
        value = payload.get(key)
        if isinstance(value, str) and value and value not in values:
            values.append(value)
    for key in ("paths", "changed_paths"):
        value = payload.get(key)
        if isinstance(value, (list, tuple, set)):
            for item in value:
                if isinstance(item, str) and item and item not in values:
                    values.append(item)
    return tuple(values)


def _truncate_observation_payload(value: Any, max_bytes: int) -> Any:
    if isinstance(value, str):
        return formatted_truncate_text(value, max_bytes)
    if isinstance(value, dict):
        return {key: _truncate_observation_payload(item, max_bytes) for key, item in value.items()}
    if isinstance(value, list):
        return [_truncate_observation_payload(item, max_bytes) for item in value]
    if isinstance(value, tuple):
        return tuple(_truncate_observation_payload(item, max_bytes) for item in value)
    return value
