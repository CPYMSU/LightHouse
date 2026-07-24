from __future__ import annotations

import re
from typing import Iterable

from .models import Capability, ConfirmationMode, KernelMode, Risk


def _terms(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", value.lower())
    words = {part for part in normalized.split() if part}
    for run in re.findall(r"[\u3400-\u9fff]+", normalized):
        words.add(run)
        words.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
    return words


def _cap(
    tool_name: str,
    command: str,
    description: str,
    kernel: KernelMode,
    executor: str,
    operation: str,
    risk: Risk,
    confirmation: ConfirmationMode,
    writes: bool,
    *,
    aliases: tuple[str, ...] = (),
    arguments: dict | None = None,
) -> Capability:
    return Capability(
        tool_name=tool_name,
        command=command,
        description=description,
        kernel=kernel,
        executor=executor,
        operation=operation,
        risk=risk,
        confirmation=confirmation,
        writes=writes,
        aliases=aliases,
        arguments=arguments or {},
    )


DEFAULT_CAPABILITIES: tuple[Capability, ...] = (
    _cap(
        "data.schema.inspect.v1", "data schema",
        "Inspect PostgreSQL schemas, tables and columns",
        KernelMode.DATA, "postgres", "schema", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("db schema", "database schema", "資料庫結構", "數據庫結構"),
        arguments={"schema": {"type": "string", "required": False}},
    ),
    _cap(
        "data.sql.query.v1", "data query",
        "Run a read-only PostgreSQL query with bound parameters",
        KernelMode.DATA, "postgres", "query", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("db query", "sql query", "查詢數據庫"),
        arguments={
            "sql": {"type": "string", "required": True},
            "params": {"type": "array", "required": False},
            "limit": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "data.sql.exec.v1", "data exec",
        "Execute a PostgreSQL mutation in one transaction and persist its receipt",
        KernelMode.DATA, "postgres", "exec", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("db exec", "sql exec", "修改數據庫", "執行 sql"),
        arguments={
            "sql": {"type": "string", "required": True},
            "params": {"type": "array", "required": False},
        },
    ),
    _cap(
        "system.project.context.v1", "project context",
        "Read the repository file index and project instruction documents",
        KernelMode.SYSTEM, "system", "project_context", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("codebase context", "project memory", "代碼庫上下文", "項目記憶"),
        arguments={
            "cwd": {"type": "string", "required": False},
            "max_files": {"type": "integer", "required": False},
            "max_instruction_bytes": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "system.file.read.v1", "file read",
        "Read a UTF-8 project file inside the selected working directory",
        KernelMode.SYSTEM, "system", "file_read", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("read file", "查看文件"),
        arguments={
            "path": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "max_bytes": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "system.file.search.v1", "file search",
        "Search project files with ripgrep-compatible text matching",
        KernelMode.SYSTEM, "system", "file_search", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("grep", "rg", "搜索代碼", "查找文件"),
        arguments={
            "query": {"type": "string", "required": True},
            "path": {"type": "string", "required": False},
            "cwd": {"type": "string", "required": False},
            "max_results": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "system.file.patch.v1", "file patch",
        "Apply one unified diff to a Git working tree using git apply",
        KernelMode.SYSTEM, "system", "file_patch", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("apply patch", "修改代碼", "套用補丁"),
        arguments={
            "patch": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "check": {"type": "boolean", "required": False},
        },
    ),
    _cap(
        "system.shell.exec.v1", "system exec",
        "Run a shell command on a local or SSH Linux target",
        KernelMode.SYSTEM, "system", "shell_exec", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("shell exec", "bash", "運行服務器命令", "執行終端指令"),
        arguments={
            "command": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "timeout": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "system.test.run.v1", "test run",
        "Run the project test command and capture its output",
        KernelMode.SYSTEM, "system", "test_run", Risk.NORMAL, ConfirmationMode.EXPLICIT, True,
        aliases=("run tests", "pytest", "運行測試"),
        arguments={
            "command": {"type": "string", "required": False},
            "cwd": {"type": "string", "required": False},
            "timeout": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "system.service.status.v1", "service status",
        "Read a systemd service status from a Linux target",
        KernelMode.SYSTEM, "system", "service_status", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("systemctl status", "服務狀態"),
        arguments={"service": {"type": "string", "required": True}},
    ),
    _cap(
        "system.service.restart.v1", "service restart",
        "Restart a systemd service and capture the command receipt",
        KernelMode.SYSTEM, "system", "service_restart", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("systemctl restart", "重啟服務"),
        arguments={"service": {"type": "string", "required": True}},
    ),
    _cap(
        "system.journal.read.v1", "journal read",
        "Read recent systemd journal lines for one service",
        KernelMode.SYSTEM, "system", "journal_read", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("journalctl", "查看日誌", "服務日誌"),
        arguments={
            "service": {"type": "string", "required": True},
            "lines": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "system.git.status.v1", "git status",
        "Read concise Git repository status on a Linux target",
        KernelMode.SYSTEM, "system", "git_status", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("查看 git 狀態",),
        arguments={"cwd": {"type": "string", "required": False}},
    ),
    _cap(
        "system.git.diff.v1", "git diff",
        "Read the current Git diff without invoking external diff tools",
        KernelMode.SYSTEM, "system", "git_diff", Risk.LOW, ConfirmationMode.DIRECT, False,
        aliases=("查看代碼修改", "show diff"),
        arguments={
            "cwd": {"type": "string", "required": False},
            "staged": {"type": "boolean", "required": False},
            "max_bytes": {"type": "integer", "required": False},
        },
    ),
    _cap(
        "system.git.commit.v1", "git commit",
        "Stage selected paths and create a Git commit",
        KernelMode.SYSTEM, "system", "git_commit", Risk.HIGH, ConfirmationMode.EXPLICIT, True,
        aliases=("提交代碼",),
        arguments={
            "message": {"type": "string", "required": True},
            "paths": {"type": "array", "required": False},
            "cwd": {"type": "string", "required": False},
        },
    ),
)


class CapabilityRegistry:
    def __init__(self, capabilities: Iterable[Capability] = DEFAULT_CAPABILITIES):
        self._capabilities = tuple(capabilities)
        self._by_name = {item.tool_name: item for item in self._capabilities}
        if len(self._by_name) != len(self._capabilities):
            raise ValueError("duplicate capability tool_name")

    def get(self, tool_name: str) -> Capability:
        try:
            return self._by_name[tool_name]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {tool_name}") from exc

    def list(self, *, kernel: KernelMode | None = None) -> list[Capability]:
        return [item for item in self._capabilities if kernel in {None, KernelMode.AUTO, item.kernel}]

    def atlas(self, *, kernel: KernelMode | None = None) -> list[dict]:
        return [item.public_dict() for item in self.list(kernel=kernel)]

    def search(self, query: str, *, kernel: KernelMode | None = None, limit: int = 20) -> list[Capability]:
        query = (query or "").strip().lower()
        query_terms = _terms(query)
        ranked: list[tuple[int, int, Capability]] = []
        for index, capability in enumerate(self.list(kernel=kernel)):
            exact_values = {
                capability.tool_name.lower(),
                capability.command.lower(),
                *(alias.lower() for alias in capability.aliases),
            }
            text = " ".join(
                [capability.tool_name, capability.command, capability.description, *capability.aliases]
            ).lower()
            if not query:
                score = 1
            elif query in exact_values:
                score = 1000
            else:
                text_terms = _terms(text)
                score = sum(30 + min(len(term), 12) for term in query_terms if term in text_terms)
                if query in text:
                    score += 200
                if not score:
                    continue
            ranked.append((-score, index, capability))
        ranked.sort(key=lambda value: (value[0], value[1]))
        return [item for _score, _index, item in ranked[: max(1, min(limit, 100))]]
