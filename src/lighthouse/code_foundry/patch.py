"""Patch-result normalisation for the LightHouse coding pipeline.

This module adapts the file-path accounting idea in OpenAI Codex's apply-patch
handler to LightHouse's unified-diff Kernel capability. It does not use Codex
patch syntax, parser, tool protocol, or runtime.

Upstream source: OpenAI Codex, commit 61a44880a85d2fd0d8770908dea5733495e571c8
  codex-rs/core/src/tools/handlers/apply_patch.rs
Copyright 2025 OpenAI. Modified and translated for LightHouse.
Licensed under the Apache License, Version 2.0. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re


_PATCH_PATH = re.compile(r"^(?:---|\+\+\+)\s+(.+?)(?:\t.*)?$")


def changed_paths_from_unified_patch(patch: str) -> tuple[str, ...]:
    """Extract ordered unique project paths from a standard unified diff.

    Both old and new headers are considered so additions and deletions retain
    a changed path. These paths are evidence metadata only; filesystem path
    validation remains the responsibility of the Kernel executor.
    """

    paths: list[str] = []
    for line in str(patch or "").splitlines():
        match = _PATCH_PATH.match(line)
        if match is None:
            continue
        path = _normalise_patch_path(match.group(1))
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


def _normalise_patch_path(value: str) -> str | None:
    path = value.strip()
    if path == "/dev/null":
        return None
    if path.startswith(("a/", "b/")):
        path = path[2:]
    return path or None
