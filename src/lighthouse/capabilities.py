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


DEFAULT_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        tool_name="data.schema.inspect.v1",
        command="data schema",
        description="Inspect PostgreSQL schemas, tables and columns",
        kernel=KernelMode.DATA,
        executor="postgres",
        operation="schema",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("db schema", "database schema", "資料庫結構", "數據庫結構"),
        arguments={"schema": {"type": "string", "required": False}},
    ),
    Capability(
        tool_name="data.sql.query.v1",
        command="data query",
        description="Run a read-only PostgreSQL query with bound parameters",
        kernel=KernelMode.DATA,
        executor="postgres",
        operation="query",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("db query", "sql query", "查詢數據庫"),
        arguments={
            "sql": {"type": "string", "required": True},
            "params": {"type": "array", "required": False},
            "limit": {"type": "integer", "required": False},
        },
    ),
    Capability(
        tool_name="data.sql.exec.v1",
        command="data exec",
        description="Execute a PostgreSQL mutation in one transaction and persist its receipt",
        kernel=KernelMode.DATA,
        executor="postgres",
        operation="exec",
        risk=Risk.HIGH,
        confirmation=ConfirmationMode.EXPLICIT,
        writes=True,
        aliases=("db exec", "sql exec", "修改數據庫", "執行 sql"),
        arguments={
            "sql": {"type": "string", "required": True},
            "params": {"type": "array", "required": False},
        },
    ),
    Capability(
        tool_name="system.shell.exec.v1",
        command="system exec",
        description="Run a shell command on a local or SSH Linux target",
        kernel=KernelMode.SYSTEM,
        executor="system",
        operation="shell_exec",
        risk=Risk.HIGH,
        confirmation=ConfirmationMode.EXPLICIT,
        writes=True,
        aliases=("shell exec", "bash", "運行服務器命令", "執行終端指令"),
        arguments={
            "command": {"type": "string", "required": True},
            "cwd": {"type": "string", "required": False},
            "timeout": {"type": "integer", "required": False},
        },
    ),
    Capability(
        tool_name="system.service.status.v1",
        command="service status",
        description="Read a systemd service status from a Linux target",
        kernel=KernelMode.SYSTEM,
        executor="system",
        operation="service_status",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("systemctl status", "服務狀態"),
        arguments={"service": {"type": "string", "required": True}},
    ),
    Capability(
        tool_name="system.service.restart.v1",
        command="service restart",
        description="Restart a systemd service and capture the command receipt",
        kernel=KernelMode.SYSTEM,
        executor="system",
        operation="service_restart",
        risk=Risk.HIGH,
        confirmation=ConfirmationMode.EXPLICIT,
        writes=True,
        aliases=("systemctl restart", "重啟服務"),
        arguments={"service": {"type": "string", "required": True}},
    ),
    Capability(
        tool_name="system.journal.read.v1",
        command="journal read",
        description="Read recent systemd journal lines for one service",
        kernel=KernelMode.SYSTEM,
        executor="system",
        operation="journal_read",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("journalctl", "查看日誌", "服務日誌"),
        arguments={
            "service": {"type": "string", "required": True},
            "lines": {"type": "integer", "required": False},
        },
    ),
    Capability(
        tool_name="system.git.status.v1",
        command="git status",
        description="Read concise Git repository status on a Linux target",
        kernel=KernelMode.SYSTEM,
        executor="system",
        operation="git_status",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("查看 git 狀態",),
        arguments={"cwd": {"type": "string", "required": False}},
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
