from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import tempfile
from typing import Any

from ..models import Capability, ExecutionResult, Target


_MAX_CONTENT_BYTES = 2_000_000


class ProjectFileExecutor:
    """Typed local project filesystem writer used by the System Kernel."""

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        if str(target.config.get("transport") or "local").lower() != "local":
            raise RuntimeError("typed project filesystem operations require a local System Target")
        if capability.operation == "file_write":
            return self._file_write(target, arguments)
        if capability.operation == "directory_create":
            return self._directory_create(target, arguments)
        raise ValueError(f"unsupported project file operation: {capability.operation}")

    def _file_write(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        relative = self._relative_path(arguments.get("path"))
        content = str(arguments.get("content") if arguments.get("content") is not None else "")
        encoded = content.encode("utf-8")
        if len(encoded) > _MAX_CONTENT_BYTES:
            raise ValueError("file content exceeds the 2 MB limit")

        cwd, destination, roots = self._resolve_destination(target, relative)
        parent = destination.parent
        if not any(parent == root or root in parent.parents for root in roots):
            raise PermissionError("file path is outside the target's allowed roots")
        if not parent.exists() or not parent.is_dir() or parent.is_symlink():
            raise FileNotFoundError("destination parent directory must be an existing regular directory")
        self._reject_symlink_chain(cwd, parent)
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

    def _directory_create(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        relative = self._relative_path(arguments.get("path"))
        cwd, destination, roots = self._resolve_destination(target, relative)
        if not any(destination == root or root in destination.parents for root in roots):
            raise PermissionError("directory path is outside the target's allowed roots")
        self._reject_symlink_chain(cwd, destination.parent)
        if destination.exists() and destination.is_symlink():
            raise PermissionError("refusing to use a symbolic-link directory")
        if destination.exists() and not destination.is_dir():
            raise FileExistsError("destination exists and is not a directory")
        existed = destination.exists()
        parents = bool(arguments.get("parents", True))
        destination.mkdir(parents=parents, exist_ok=True)
        return ExecutionResult(
            ok=True,
            result={
                "path": str(destination),
                "relative_path": relative,
                "created": not existed,
                "already_existed": existed,
            },
            exit_code=0,
        )

    @staticmethod
    def _resolve_destination(target: Target, relative: str) -> tuple[Path, Path, list[Path]]:
        cwd = Path(str(target.config.get("default_cwd") or "/")).expanduser().resolve()
        destination = cwd.joinpath(*PurePosixPath(relative).parts)
        roots = [Path(str(item)).expanduser().resolve() for item in (target.config.get("allowed_roots") or [cwd])]
        return cwd, destination, roots

    @staticmethod
    def _reject_symlink_chain(cwd: Path, destination_parent: Path) -> None:
        try:
            relative = destination_parent.relative_to(cwd)
        except ValueError:
            return
        current = cwd
        for part in relative.parts:
            current = current / part
            if current.exists() and current.is_symlink():
                raise PermissionError("directory path may not traverse a symbolic link")

    @staticmethod
    def _relative_path(value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/")
        if not text or text.startswith("/") or "\x00" in text:
            raise ValueError("path must be a non-empty relative project path")
        parts = PurePosixPath(text).parts
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("path may not contain dot or parent segments")
        return "/".join(parts)
