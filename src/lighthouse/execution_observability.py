from __future__ import annotations

import re
from typing import Any

from rich.table import Table
from rich.text import Text

from .background_intelligence import BackgroundIntelligenceWorker
from .ui import AMBER, CYAN, GREEN, INK_2, INK_3, INK_4, RED, _message_text, _short
from .ui_v12 import ObservatoryTerminal


_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}\b"), "[REDACTED]"),
    (
        re.compile(r"(?i)((?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[:=]\s*)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"), r"\1[REDACTED]\2"),
)


def _redact(value: Any, limit: int = 360) -> str:
    text = str(value if value is not None else "")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def describe_tool_call(capability: str, arguments: dict[str, Any]) -> tuple[str, str]:
    capability = str(capability or "")
    arguments = arguments if isinstance(arguments, dict) else {}
    if capability == "system.file.read.v1":
        return "READ", _redact(arguments.get("path") or "project file")
    if capability == "system.file.search.v1":
        summary = _redact(arguments.get("query") or "code search")
        if arguments.get("path"):
            summary += " in " + _redact(arguments.get("path"))
        return "SEARCH", summary
    if capability == "system.file.patch.v1":
        paths = []
        for line in str(arguments.get("patch") or "").splitlines():
            if line.startswith("+++ b/"):
                path = line[6:].strip()
                if path and path != "/dev/null" and path not in paths:
                    paths.append(path)
        return "EDIT", _redact(", ".join(paths[:8]) or "apply code patch")
    if capability.startswith("system.file.write"):
        return "WRITE", _redact(arguments.get("path") or "project file")
    if capability == "system.test.run.v1":
        return "TEST", _redact(arguments.get("command") or "project tests")
    if capability == "system.git.diff.v1":
        return "DIFF", "review current Git diff"
    if capability == "system.git.status.v1":
        return "GIT", "inspect repository status"
    if capability == "system.project.context.v1":
        return "CONTEXT", "inspect project files, entry points and instructions"
    if capability == "system.shell.exec.v1":
        return "EXEC", _redact(arguments.get("command") or "shell command")
    if capability.startswith("data.sql.query"):
        return "DATABASE", _redact(arguments.get("query") or arguments.get("sql") or "inspect PostgreSQL data")
    if capability.startswith("data.sql.exec"):
        return "DATABASE", _redact(arguments.get("query") or arguments.get("sql") or "execute PostgreSQL transaction")
    if capability.startswith("system.service"):
        return "SERVICE", _redact(arguments.get("service") or capability)
    if capability.startswith("agent.bus"):
        return "AGENT", _redact(arguments.get("role") or arguments.get("goal") or capability)
    if capability.startswith("desktop."):
        return "DESKTOP", _redact(arguments.get("url") or arguments.get("path") or capability)
    return "TOOL", _redact(capability)


class ObservableBackgroundIntelligenceWorker(BackgroundIntelligenceWorker):
    """Specialist worker that emits safe start/result events for every real tool call."""

    def _execute_specialist_tool(
        self,
        *,
        work_order: dict[str, Any],
        agent: dict[str, Any],
        call: Any,
        round_index: int,
        call_index: int,
        project_id: str | None,
        parent_run_id: str | None,
    ) -> dict[str, Any]:
        capability = str(call.get("capability") or "") if isinstance(call, dict) else ""
        arguments = call.get("arguments") if isinstance(call, dict) and isinstance(call.get("arguments"), dict) else {}
        label, summary = describe_tool_call(capability, arguments)
        self.agent_bus.append_work_event(
            work_order["id"],
            "agent_tool_started",
            {
                "round": round_index,
                "call_index": call_index,
                "capability": capability,
                "label": label,
                "summary": summary,
                "status": "running",
            },
        )
        try:
            result = super()._execute_specialist_tool(
                work_order=work_order,
                agent=agent,
                call=call,
                round_index=round_index,
                call_index=call_index,
                project_id=project_id,
                parent_run_id=parent_run_id,
            )
        except Exception as exc:
            self.agent_bus.append_work_event(
                work_order["id"],
                "agent_tool_completed",
                {
                    "round": round_index,
                    "call_index": call_index,
                    "capability": capability,
                    "label": label,
                    "summary": summary,
                    "status": "failed",
                    "error": _redact(exc, 500),
                },
            )
            raise
        receipt = result.get("receipt") if isinstance(result.get("receipt"), dict) else {}
        operation = result.get("operation") if isinstance(result.get("operation"), dict) else {}
        status = "succeeded" if result.get("ok") else "permission_required" if result.get("permission_required") else "failed"
        self.agent_bus.append_work_event(
            work_order["id"],
            "agent_tool_completed",
            {
                "round": round_index,
                "call_index": call_index,
                "capability": capability,
                "label": label,
                "summary": summary,
                "status": status,
                "operation_id": operation.get("id") or result.get("operation_id"),
                "receipt_ok": receipt.get("ok"),
                "result_hash": receipt.get("result_hash"),
                "error": _redact(result.get("error") or "", 500),
            },
        )
        return result


class AgentExecutionContextMixin:
    """Expose specialist tool activity to both the terminal and the next main-AI turn."""

    def _agent_execution_activity(self, run_id: str, *, limit: int = 160) -> list[dict[str, Any]]:
        agent_bus = getattr(self, "agent_bus", None)
        if agent_bus is None or not hasattr(agent_bus, "run_activity"):
            return []
        run = self.repository.get_agent_run(run_id)
        try:
            return agent_bus.run_activity(
                workspace_id=run.workspace_id,
                parent_run_id=run_id,
                limit=limit,
            )
        except Exception:
            return []

    def snapshot(self, run_id: str) -> dict[str, Any]:
        snapshot = super().snapshot(run_id)
        snapshot["agent_execution_activity"] = self._agent_execution_activity(run_id)
        return snapshot

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        activity = self._agent_execution_activity(run_id, limit=80)
        state["agent_execution_activity"] = {
            "recent": activity[-40:],
            "instruction": (
                "These are real specialist tool events. Successful/failed status is Receipt-backed; "
                "use them to avoid repeating work and to continue from the latest execution evidence."
            ),
        }
        continuity = state.get("cognitive_continuity")
        if isinstance(continuity, dict):
            continuity["recent_agent_activity"] = activity[-24:]
        return state


class ExecutionObservatoryTerminal(ObservatoryTerminal):
    """Claude-Code-style live execution stream over durable LightHouse events."""

    def __init__(self, console=None, *, observe_mode: str = "balanced"):
        super().__init__(console=console, observe_mode=observe_mode)
        self._agent_execution_seen: set[int] = set()

    def render_run(
        self,
        snapshot: dict[str, Any],
        *,
        seen: set[tuple[Any, ...]] | None = None,
    ) -> set[tuple[Any, ...]]:
        seen = super().render_run(snapshot, seen=seen)
        self._render_agent_execution(snapshot)
        return seen

    def _visible_activity(self, item: dict[str, Any]) -> bool:
        status = str(item.get("status") or "")
        label = str(item.get("label") or "")
        importance = str(item.get("importance") or "normal")
        if status == "failed":
            return True
        if self.observe_mode == "verbose":
            return True
        if self.observe_mode == "balanced":
            return bool(label)
        if self.observe_mode == "focus":
            return importance == "critical" or label in {"EDIT", "WRITE", "TEST", "DIFF", "EXEC", "DATABASE", "SERVICE"}
        return False

    def _render_activity(self, snapshot: dict[str, Any], sequences: set[int]) -> None:
        observer = snapshot.get("cognitive_observer")
        if not isinstance(observer, dict):
            return
        all_items = [item for item in observer.get("activity") or [] if isinstance(item, dict)]
        items = [
            item for item in all_items
            if int(item.get("sequence") or 0) in sequences and self._visible_activity(item)
        ]
        if not items:
            return
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(width=10)
        table.add_column(ratio=1, overflow="fold")
        table.add_column(width=12, justify="right")
        for item in items:
            status = str(item.get("status") or "requested")
            label = str(item.get("label") or "TOOL")
            summary = str(item.get("summary") or item.get("capability") or "—")
            if label in {"PASS", "FAIL", "RESULT"}:
                capability = str(item.get("capability") or "")
                prior = next(
                    (
                        candidate
                        for candidate in reversed(all_items)
                        if int(candidate.get("sequence") or 0) < int(item.get("sequence") or 0)
                        and str(candidate.get("capability") or "") == capability
                        and str(candidate.get("label") or "") not in {"PASS", "FAIL", "RESULT"}
                    ),
                    None,
                )
                if prior:
                    label = str(prior.get("label") or label)
                    summary = str(prior.get("summary") or summary)
            display_status = "STARTED" if status == "requested" else status.upper()
            color = RED if status == "failed" else GREEN if status == "succeeded" else AMBER if status == "permission_required" else CYAN
            table.add_row(
                Text(label, style=f"bold {color}"),
                _message_text(summary, INK_2),
                Text(display_status, style=INK_4),
            )
        self.console.print(table)
        self.console.print()

    def _render_agent_execution(self, snapshot: dict[str, Any]) -> None:
        values = snapshot.get("agent_execution_activity")
        if not isinstance(values, list) or self.observe_mode == "off":
            return
        fresh = []
        for item in values:
            if not isinstance(item, dict):
                continue
            event_id = int(item.get("id") or 0)
            if event_id <= 0 or event_id in self._agent_execution_seen:
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            status = str(payload.get("status") or "")
            label = str(payload.get("label") or "TOOL")
            if self.observe_mode == "focus" and status != "failed" and label not in {
                "EDIT", "WRITE", "TEST", "DIFF", "EXEC", "DATABASE", "SERVICE"
            }:
                self._agent_execution_seen.add(event_id)
                continue
            fresh.append(item)
            self._agent_execution_seen.add(event_id)
        if not fresh:
            return
        table = Table.grid(expand=True, padding=(0, 1))
        table.add_column(width=14)
        table.add_column(width=9)
        table.add_column(ratio=1, overflow="fold")
        table.add_column(width=12, justify="right")
        for item in fresh:
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            status = str(payload.get("status") or "running")
            role = str(item.get("role") or "agent").upper()
            label = str(payload.get("label") or "TOOL")
            summary = _short(payload.get("summary") or payload.get("capability") or "—", 180)
            color = RED if status == "failed" else GREEN if status == "succeeded" else AMBER if status == "permission_required" else CYAN
            table.add_row(
                Text("A:" + role[:12], style=f"bold {INK_3}"),
                Text(label, style=f"bold {color}"),
                _message_text(summary, INK_2),
                Text(status.upper(), style=INK_4),
            )
        self.console.print(table)
        self.console.print()
