from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any

from ..models import Capability, ExecutionResult, Target


_MAX_CONTENT_BYTES = 2_000_000


class ProjectFileExecutor:
    """Typed local project-file writer used by the System Kernel."""

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        if capability.operation != "file_write":
            raise ValueError(f"unsupported project file operation: {capability.operation}")
        if str(target.config.get("transport") or "local").lower() != "local":
            raise RuntimeError("typed project file writing currently requires a local System Target")

        relative = self._relative_path(arguments.get("path"))
        content = str(arguments.get("content") if arguments.get("content") is not None else "")
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_CONTENT_BYTES:
            raise ValueError("file content exceeds the 2 MB limit")

        cwd = Path(str(target.config.get("default_cwd") or "/")).expanduser().resolve()
        destination = cwd.joinpath(*PurePosixPath(relative).parts)
        parent = destination.parent.resolve()
        roots = [Path(str(item)).expanduser().resolve() for item in (target.config.get("allowed_roots") or [cwd])]
        if not any(parent == root or root in parent.parents for root in roots):
            raise PermissionError("file path is outside the target's allowed roots")
        if not parent.exists() or not parent.is_dir() or parent.is_symlink():
            raise FileNotFoundError("destination parent directory must be an existing regular directory")
        if destination.exists() and destination.is_symlink():
            raise PermissionError("refusing to replace a symbolic link")
        existed = destination.exists()
        overwrite = bool(arguments.get("overwrite", False))
        if existed and not overwrite:
            raise FileExistsError("destination already exists; set overwrite=true for this frozen operation")

        descriptor, temporary_name = tempfile.mkstemp(prefix=".lighthouse-write-", dir=parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o644)
            os.replace(temporary_name, destination)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

        result = {
            "path": str(destination),
            "relative_path": relative,
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "created": not existed,
            "replaced": existed,
        }
        return ExecutionResult(ok=True, result=result, exit_code=0)

    @staticmethod
    def _relative_path(value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or "\x00" in text:
            raise ValueError("path must be a non-empty relative project path")
        parts = PurePosixPath(text).parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("path may not contain dot or parent segments")
        return "/".join(parts)
