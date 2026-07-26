"""Bounded, incremental model context for the CodeFoundry tool surface.

This is a LightHouse-native Python adaptation of the tool-state rendering
algorithm in OpenAI Codex.  It deliberately describes LightHouse actions,
rather than importing Codex protocol types or its runtime interface.

Upstream source: OpenAI Codex, commit 61a44880a85d2fd0d8770908dea5733495e571c8
  codex-rs/core/src/context/world_state/tools.rs
Copyright 2025 OpenAI.  Modified and translated for LightHouse.
Licensed under the Apache License, Version 2.0.  See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from html import escape

from .tools import CodeToolSpec


MAX_TOOL_DESCRIPTION_CHARS = 250
MAX_RENDERED_TOOL_CONTEXT_BYTES = 4 * 1024
OMITTED_LINE_RESERVE_BYTES = 64
_OPEN_TAG = "<code_tools>"
_CLOSE_TAG = "</code_tools>"


class CodeToolContext:
    """Renders a compact full snapshot once, then only tool-surface changes."""

    def __init__(self, specs: tuple[CodeToolSpec, ...]):
        self._tools = {
            spec.kind.value: _normalise_description(spec)
            for spec in sorted(specs, key=lambda item: item.kind.value)
        }

    def snapshot(self) -> dict[str, str]:
        """Return a copy suitable for retaining as the next-turn baseline."""

        return dict(self._tools)

    def render_diff(self, previous: Mapping[str, str] | None) -> str | None:
        """Render the current tool surface or its delta from ``previous``.

        ``None`` represents an unknown/absent baseline.  An unchanged surface
        produces no model text.  Description changes intentionally count as
        additions, so a capability or execution-mode change is visible.
        """

        current = self._tools
        if previous is not None and dict(previous) == current:
            return None
        if previous is None and not current:
            return None

        if previous is None:
            body = _render_groups(
                (("Available CodeFoundry tools", current),),
                current_is_empty=not current,
            )
        else:
            added = {
                name: description
                for name, description in current.items()
                if previous.get(name) != description
            }
            removed = {
                name: description
                for name, description in previous.items()
                if name not in current
            }
            body = _render_groups(
                (
                    ("Added or updated CodeFoundry tools", added),
                    ("Removed CodeFoundry tools", removed),
                ),
                current_is_empty=not current,
            )
        return f"{_OPEN_TAG}{body}{_CLOSE_TAG}"


def _normalise_description(spec: CodeToolSpec) -> str:
    first_line = spec.description.splitlines()[0].strip() if spec.description else ""
    capability = spec.capability or "native-review"
    execution = "parallel" if spec.supports_parallel else "serial"
    workspace = "mutates workspace" if spec.mutates_workspace else "read-only"
    details = f"{first_line} [{capability}; {execution}; {workspace}]".strip()
    return details[:MAX_TOOL_DESCRIPTION_CHARS]


def _render_groups(
    groups: tuple[tuple[str, Mapping[str, str]], ...],
    *,
    current_is_empty: bool,
) -> str:
    body_budget = MAX_RENDERED_TOOL_CONTEXT_BYTES - _byte_len(_OPEN_TAG) - _byte_len(_CLOSE_TAG)
    empty_state = "No CodeFoundry tools remain.\n" if current_is_empty else None
    fixed_bytes = (
        1
        + sum(
            _byte_len(label) + _byte_len(":\n") + OMITTED_LINE_RESERVE_BYTES
            for label, tools in groups
            if tools
        )
        + (0 if empty_state is None else _byte_len(empty_state))
    )
    remaining_entry_bytes = max(0, body_budget - fixed_bytes)
    rendered = "\n"

    for label, tools in groups:
        if not tools:
            continue
        rendered += f"{label}:\n"
        omitted = 0
        for name, description in sorted(tools.items()):
            entry = _render_tool(name, description)
            if _byte_len(entry) <= remaining_entry_bytes:
                remaining_entry_bytes -= _byte_len(entry)
                rendered += entry
            else:
                omitted += 1
        if omitted:
            rendered += f"... {omitted} additional tools omitted.\n"

    if empty_state is not None:
        rendered += empty_state
    return rendered


def _render_tool(name: str, description: str) -> str:
    rendered = f"- {escape(name, quote=False)}"
    if description:
        rendered += f": {escape(description, quote=False)}"
    return f"{rendered}\n"


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8"))
