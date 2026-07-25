from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .models import KernelMode


class ExecutionAddressResolver:
    """Validate model-selected execution coordinates without choosing a subject."""

    def __init__(self, memory, repository, target_resolver=None):
        self.memory = memory
        self.repository = repository
        self.target_resolver = target_resolver

    def ground(self, *, run, capability, arguments: dict[str, Any]) -> dict[str, Any]:
        value = dict(arguments or {})
        if capability.executor == "agent_bus":
            return value
        if capability.kernel not in {KernelMode.SYSTEM, KernelMode.DESKTOP}:
            return value
        workspace = self.repository.get_workspace(run.workspace_id)
        if self.target_resolver is not None:
            target_id = self.target_resolver.resolve(
                workspace,
                capability.kernel,
                value,
            )
        else:
            target_id = (
                workspace.system_target_id
                if capability.kernel == KernelMode.SYSTEM
                else workspace.desktop_target_id
            )
        if not target_id:
            return value
        target = self.repository.get_target(target_id)
        default_cwd = Path(
            str(target.config.get("default_cwd") or "/")
        ).expanduser().resolve()
        roots = [
            Path(str(item)).expanduser().resolve()
            for item in (target.config.get("allowed_roots") or [default_cwd])
        ]
        if capability.kernel == KernelMode.SYSTEM:
            return self._validate_system(
                capability.operation,
                value,
                default_cwd=default_cwd,
                roots=roots,
            )
        if capability.operation == "open_file":
            return self._validate_desktop_file(
                value,
                default_cwd=default_cwd,
                roots=roots,
            )
        return value

    def _validate_system(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        default_cwd: Path,
        roots: list[Path],
    ) -> dict[str, Any]:
        value = dict(arguments)
        if operation == "directory_create":
            path = str(value.get("path") or "").strip()
            if not path or Path(path).is_absolute():
                raise ValueError(
                    "new directories require a relative path inside the bound Workspace"
                )
            value["path"] = self._safe_relative(path)
            value.pop("cwd", None)
            return value

        proposed_cwd = str(value.get("cwd") or "").strip()
        cwd_raw = Path(proposed_cwd).expanduser() if proposed_cwd else default_cwd
        if cwd_raw.is_symlink():
            raise ValueError("execution cwd may not be a symbolic link")
        cwd = cwd_raw.resolve()
        self._require_inside(cwd, roots, label="execution cwd")
        if not cwd.exists() or not cwd.is_dir():
            raise ValueError("execution cwd is not a real directory")
        value["cwd"] = str(cwd)

        if operation in {"file_read", "file_write"}:
            raw = str(value.get("path") or "").strip()
            if not raw:
                raise ValueError("file path is required")
            candidate = self._candidate(cwd, raw)
            self._require_inside(candidate, roots, label="file path")
            unresolved = Path(raw).expanduser()
            raw_candidate = unresolved if unresolved.is_absolute() else cwd / unresolved
            if raw_candidate.is_symlink():
                raise ValueError("file path may not be a symbolic link")
            if operation == "file_read":
                if not candidate.exists() or not candidate.is_file():
                    raise ValueError("file path does not exist")
            else:
                parent = candidate.parent
                if parent.is_symlink() or not parent.exists() or not parent.is_dir():
                    raise ValueError("file parent directory is not real")
                if candidate.exists() and (candidate.is_symlink() or not candidate.is_file()):
                    raise ValueError("file write target is not a regular file")
            value["path"] = self._relative(cwd, candidate)
        elif operation == "file_search":
            raw = str(value.get("path") or "").strip()
            if raw:
                candidate = self._candidate(cwd, raw)
                self._require_inside(candidate, roots, label="search path")
                unresolved = Path(raw).expanduser()
                raw_candidate = unresolved if unresolved.is_absolute() else cwd / unresolved
                if raw_candidate.is_symlink():
                    raise ValueError("search path may not be a symbolic link")
                if not candidate.exists() or not candidate.is_dir():
                    raise ValueError("search path is not a real directory")
                value["path"] = self._relative(cwd, candidate)
            else:
                value["path"] = ""
        return value

    def _validate_desktop_file(
        self,
        arguments: dict[str, Any],
        *,
        default_cwd: Path,
        roots: list[Path],
    ) -> dict[str, Any]:
        value = dict(arguments)
        raw = str(value.get("path") or "").strip()
        if not raw:
            raise ValueError(
                "desktop file path is required; choose it from context or investigate first"
            )
        unresolved = Path(raw).expanduser()
        raw_candidate = (
            unresolved if unresolved.is_absolute() else default_cwd / unresolved
        )
        if raw_candidate.is_symlink():
            raise ValueError("desktop file may not be a symbolic link")
        candidate = raw_candidate.resolve()
        self._require_inside(candidate, roots, label="desktop file")
        if not candidate.exists() or not candidate.is_file():
            raise ValueError("desktop file does not exist")
        value["path"] = str(candidate)
        return value

    @staticmethod
    def _candidate(cwd: Path, raw: str) -> Path:
        path = Path(str(raw or "")).expanduser()
        if not path.is_absolute() and ".." in path.parts:
            raise ValueError("relative path may not contain parent segments")
        return path.resolve() if path.is_absolute() else (cwd / path).resolve()

    @staticmethod
    def _relative(cwd: Path, candidate: Path) -> str:
        try:
            return candidate.relative_to(cwd).as_posix()
        except ValueError as exc:
            raise ValueError("file is not below the selected execution cwd") from exc

    @staticmethod
    def _safe_relative(value: str) -> str:
        parts = PurePosixPath(value.replace("\\", "/")).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("relative path may not contain dot or parent segments")
        return "/".join(parts)

    @staticmethod
    def _require_inside(path: Path, roots: list[Path], *, label: str) -> None:
        if not any(path == root or root in path.parents for root in roots):
            raise PermissionError(f"{label} is outside the bound Workspace roots")
