from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .models import KernelMode

_REFERENTIAL_MARKERS = (
    "繼續", "继续", "再", "剛才", "刚才", "之前", "這個", "这个", "那個", "那个",
    "它", "豐富", "丰富", "優化", "优化", "continue", "again", "more", "richer",
    "this", "that", "previous", "before",
)


class ExecutionAddressResolver:
    """Resolve model-proposed paths against durable, observed locators."""

    def __init__(self, memory, repository, target_resolver=None):
        self.memory = memory
        self.repository = repository
        self.target_resolver = target_resolver

    def ground(self, *, run, capability, arguments: dict[str, Any]) -> dict[str, Any]:
        value = dict(arguments or {})
        if capability.kernel not in {KernelMode.SYSTEM, KernelMode.DESKTOP}:
            return value
        workspace = self.repository.get_workspace(run.workspace_id)
        if self.target_resolver is not None:
            target_id = self.target_resolver.resolve(workspace, capability.kernel, value)
        else:
            target_id = workspace.system_target_id if capability.kernel == KernelMode.SYSTEM else workspace.desktop_target_id
        if not target_id:
            return value
        target = self.repository.get_target(target_id)
        default_cwd = Path(str(target.config.get("default_cwd") or "/")).expanduser().resolve()
        roots = [Path(str(item)).expanduser().resolve() for item in (target.config.get("allowed_roots") or [default_cwd])]
        conversation = self.memory.conversation_for_run(run.id)
        memory = self.memory.context(workspace_id=run.workspace_id, actor=run.actor, conversation_id=conversation["id"] if conversation else None, query=run.task, message_limit=8, file_limit=30)
        active_subject = self._active_subject(memory)
        known_files = {Path(str(item["canonical_path"])).expanduser().resolve() for item in memory.get("relevant_files") or [] if item.get("canonical_path")}
        known_locators = [(str(item.get("kind") or ""), Path(str(item.get("canonical_value"))).expanduser()) for item in memory.get("recent_locators") or [] if item.get("canonical_value") and str(item.get("kind") or "") in {"file", "directory"}]
        known_directories = {default_cwd}
        for kind, raw in known_locators:
            path = raw.resolve()
            known_directories.add(path if kind == "directory" else path.parent)
        for path in known_files:
            known_directories.add(path.parent)
        if active_subject:
            known_directories.add(active_subject if active_subject.is_dir() else active_subject.parent)
            if active_subject.is_file():
                known_files.add(active_subject)
        if capability.kernel == KernelMode.SYSTEM:
            return self._ground_system(capability.operation, value, default_cwd=default_cwd, roots=roots, active_subject=active_subject, known_files=known_files, known_directories=known_directories)
        if capability.operation == "open_file":
            return self._ground_desktop_file(value, default_cwd=default_cwd, roots=roots, active_subject=active_subject, known_files=known_files)
        return value

    def _ground_system(self, operation: str, arguments: dict[str, Any], *, default_cwd: Path, roots: list[Path], active_subject: Path | None, known_files: set[Path], known_directories: set[Path]) -> dict[str, Any]:
        value = dict(arguments)
        if operation == "directory_create":
            path = str(value.get("path") or "").strip()
            if not path or Path(path).is_absolute():
                raise ValueError("new directories require a relative path inside the bound Workspace")
            value["path"] = self._safe_relative(path)
            value.pop("cwd", None)
            return value
        proposed_cwd = str(value.get("cwd") or "").strip()
        if proposed_cwd:
            cwd = Path(proposed_cwd).expanduser().resolve()
            if not self._inside(cwd, roots):
                raise PermissionError("execution cwd is outside the bound Workspace roots")
            if not cwd.exists() or not cwd.is_dir() or cwd.is_symlink():
                raise ValueError("execution cwd is not a real indexed directory; use the active subject or Workspace root")
            if cwd not in known_directories:
                raise ValueError("execution cwd was not observed in conversation memory, file index, or a successful Receipt")
        else:
            cwd = self._preferred_directory(active_subject, known_directories, default_cwd, roots)
        value["cwd"] = str(cwd)
        if operation in {"file_read", "file_write"}:
            value["path"] = self._ground_existing_or_active_file(value.get("path"), cwd=cwd, active_subject=active_subject, known_files=known_files, must_exist=operation == "file_read")
        elif operation == "file_search":
            path = str(value.get("path") or "").strip()
            if path:
                candidate = self._relative_candidate(cwd, path)
                if not candidate.exists() or not candidate.is_dir():
                    raise ValueError("search path is not a real directory")
                if candidate not in known_directories:
                    raise ValueError("search path is not grounded in the file index or prior Receipts")
                value["path"] = self._relative(cwd, candidate)
            else:
                value["path"] = ""
        return value

    def _ground_desktop_file(self, arguments: dict[str, Any], *, default_cwd: Path, roots: list[Path], active_subject: Path | None, known_files: set[Path]) -> dict[str, Any]:
        value = dict(arguments)
        raw = str(value.get("path") or "").strip()
        candidate = None
        if active_subject and active_subject.is_file() and (not raw or Path(raw).name == active_subject.name or not self._relative_candidate(default_cwd, raw).exists()):
            candidate = active_subject
        elif raw:
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = default_cwd / candidate
            candidate = candidate.resolve()
        if candidate is None or not candidate.exists() or not candidate.is_file():
            raise ValueError("desktop file address is unresolved; use an indexed file or successful Receipt path")
        if not self._inside(candidate, roots):
            raise PermissionError("desktop file is outside the bound Workspace roots")
        if candidate not in known_files and candidate != active_subject:
            raise ValueError("desktop file address was not observed in memory or the file index")
        value["path"] = str(candidate)
        return value

    def _ground_existing_or_active_file(self, raw_value: Any, *, cwd: Path, active_subject: Path | None, known_files: set[Path], must_exist: bool) -> str:
        raw = str(raw_value or "").strip()
        active_file = active_subject if active_subject and active_subject.is_file() else None
        candidate = self._relative_candidate(cwd, raw) if raw else None
        if active_file and (not raw or Path(raw).name == active_file.name or (candidate is not None and not candidate.exists())):
            candidate = active_file
        if candidate is None:
            raise ValueError("file path is unresolved; use the active file from memory or an indexed path")
        if must_exist and (not candidate.exists() or not candidate.is_file()):
            raise ValueError("file path does not exist")
        if candidate.exists() and candidate not in known_files and candidate != active_file:
            raise ValueError("file path was not observed in conversation memory, the file index, or a successful Receipt")
        return self._relative(cwd, candidate)

    @staticmethod
    def _active_subject(memory: dict[str, Any]) -> Path | None:
        task = memory.get("active_task") if isinstance(memory.get("active_task"), dict) else {}
        conversation = memory.get("conversation") if isinstance(memory.get("conversation"), dict) else {}
        raw = task.get("subject")
        if not raw:
            goal = str(task.get("goal") or "").lower()
            if any(marker in goal for marker in _REFERENTIAL_MARKERS):
                raw = conversation.get("active_subject_value")
        if not raw:
            return None
        try:
            return Path(str(raw)).expanduser().resolve()
        except OSError:
            return None

    @staticmethod
    def _preferred_directory(active_subject: Path | None, known: set[Path], default: Path, roots: list[Path]) -> Path:
        candidate = active_subject if active_subject and active_subject.is_dir() else active_subject.parent if active_subject else None
        if candidate and candidate.exists() and candidate in known and ExecutionAddressResolver._inside(candidate, roots):
            return candidate
        return default

    @staticmethod
    def _relative_candidate(cwd: Path, raw: str) -> Path:
        path = Path(str(raw or "")).expanduser()
        return path.resolve() if path.is_absolute() else (cwd / path).resolve()

    @staticmethod
    def _relative(cwd: Path, candidate: Path) -> str:
        try:
            return candidate.relative_to(cwd).as_posix()
        except ValueError as exc:
            raise ValueError("file is not below the grounded execution cwd") from exc

    @staticmethod
    def _safe_relative(value: str) -> str:
        parts = PurePosixPath(value.replace("\\", "/")).parts
        if not parts or any(part in {"", ".", ".."} for part in parts):
            raise ValueError("relative path may not contain dot or parent segments")
        return "/".join(parts)

    @staticmethod
    def _inside(path: Path, roots: list[Path]) -> bool:
        return any(path == root or root in path.parents for root in roots)
