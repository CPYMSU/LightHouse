"""UTF-8-safe context truncation for CodeFoundry observations.

This module adapts the middle-truncation algorithm in OpenAI Codex to the
LightHouse history model.  It contains no Codex protocol or runtime code.

Upstream sources: OpenAI Codex, commit 61a44880a85d2fd0d8770908dea5733495e571c8
  codex-rs/utils/string/src/truncate.rs
  codex-rs/utils/output-truncation/src/lib.rs
Copyright 2025 OpenAI.  Modified and translated for LightHouse.
Licensed under the Apache License, Version 2.0.  See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations


APPROX_BYTES_PER_TOKEN = 4


def approx_token_count(text: str) -> int:
    """Use the same bounded four-byte-per-token estimate as the upstream code."""

    return (_byte_len(text) + APPROX_BYTES_PER_TOKEN - 1) // APPROX_BYTES_PER_TOKEN


def truncate_middle_chars(text: str, max_bytes: int) -> str:
    """Preserve prefix and suffix without splitting a UTF-8 code point."""

    return _truncate_with_byte_estimate(text, max_bytes, use_tokens=False)


def truncate_middle_with_token_budget(text: str, max_tokens: int) -> tuple[str, int | None]:
    """Return a middle-truncated value and original estimated tokens if changed."""

    if not text:
        return "", None
    budget = max(0, int(max_tokens))
    if budget > 0 and _byte_len(text) <= budget * APPROX_BYTES_PER_TOKEN:
        return text, None

    truncated = _truncate_with_byte_estimate(
        text,
        budget * APPROX_BYTES_PER_TOKEN,
        use_tokens=True,
    )
    return truncated, (None if truncated == text else approx_token_count(text))


def formatted_truncate_text(text: str, max_bytes: int) -> str:
    """Describe truncation so the model knows that an observation is incomplete."""

    budget = max(0, int(max_bytes))
    if _byte_len(text) <= budget:
        return text
    result = truncate_middle_chars(text, budget)
    return (
        f"Warning: truncated output (original token count: {approx_token_count(text)})\n"
        f"Total output lines: {len(text.splitlines())}\n\n{result}"
    )


def _truncate_with_byte_estimate(text: str, max_bytes: int, *, use_tokens: bool) -> str:
    if not text:
        return ""

    total_bytes = _byte_len(text)
    total_chars = len(text)
    budget = max(0, int(max_bytes))
    if budget == 0:
        return _format_marker(use_tokens, _removed_units(use_tokens, total_bytes, total_chars))
    if total_bytes <= budget:
        return text

    left_budget = budget // 2
    right_budget = budget - left_budget
    removed_chars, prefix, suffix = _split_string(text, left_budget, right_budget)
    marker = _format_marker(
        use_tokens,
        _removed_units(use_tokens, total_bytes - budget, removed_chars),
    )
    return f"{prefix}{marker}{suffix}"


def _split_string(text: str, beginning_bytes: int, end_bytes: int) -> tuple[int, str, str]:
    total_bytes = _byte_len(text)
    tail_start_target = max(0, total_bytes - end_bytes)
    byte_offset = 0
    prefix_end = 0
    suffix_start = len(text)
    removed_chars = 0
    suffix_started = False

    for index, char in enumerate(text):
        char_end = byte_offset + _byte_len(char)
        if char_end <= beginning_bytes:
            prefix_end = index + 1
        elif byte_offset >= tail_start_target:
            if not suffix_started:
                suffix_start = index
                suffix_started = True
        else:
            removed_chars += 1
        byte_offset = char_end

    suffix_start = max(suffix_start, prefix_end)
    return removed_chars, text[:prefix_end], text[suffix_start:]


def _format_marker(use_tokens: bool, removed_count: int) -> str:
    unit = "tokens" if use_tokens else "chars"
    return f"…{removed_count} {unit} truncated…"


def _removed_units(use_tokens: bool, removed_bytes: int, removed_chars: int) -> int:
    if use_tokens:
        return (removed_bytes + APPROX_BYTES_PER_TOKEN - 1) // APPROX_BYTES_PER_TOKEN
    return removed_chars


def _byte_len(value: str) -> int:
    return len(value.encode("utf-8"))
