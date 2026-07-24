from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
from typing import Any, Iterable

from rich import box
from rich.console import Console, Group
from rich.json import JSON
from rich.panel import Panel
from rich.prompt import Confirm
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.completion import WordCompleter
    from prompt_toolkit.formatted_text import HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.styles import Style
except ImportError:  # pragma: no cover
    PromptSession = None


PAPER = "bright_white"
INK_2 = "grey82"
INK_3 = "grey58"
INK_4 = "grey42"
RED = "red3"
GREEN = "green3"
AMBER = "yellow3"
CYAN = "cyan3"

_LOCAL_COMMANDS = (
    "/help",
    "/status",
    "/capabilities",
    "/mode auto",
    "/mode system",
    "/mode data",
    "/init",
    "/doctor",
    "/login",
    "/clear",
    "/exit",
)

_STAGE = {
    "run_created": ("PLAN", "01", INK_2),
    "project_context": ("CONTEXT", "02", CYAN),
    "decision": ("THINK", "03", PAPER),
    "operation_dispatched": ("EXECUTE", "04", RED),
    "auto_confirmation": ("CONFIRM", "05", AMBER),
    "observation": ("VERIFY", "06", GREEN),
    "input_required": ("INPUT", "07", AMBER),
    "protocol_error": ("PROTOCOL", "!", RED),
    "provider_error": ("PROVIDER", "!", RED),
    "tool_rejected": ("REJECTED", "!", RED),
    "run_failed": ("FAILED", "!", RED),
    "run_completed": ("COMPLETE", "08", GREEN),
    "user_input": ("INPUT", "07", CYAN),
}


def _text(value: Any, style: str | None = None, *, max_chars: int | None = None) -> Text:
    string = "—" if value is None or value == "" else str(value)
    if max_chars and len(string) > max_chars:
        string = string[: max_chars - 1] + "…"
    return Text(string, style=style or "")


def _short(value: Any, limit: int = 180) -> str:
    if value is None or value == "":
        return "—"
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError):
            text = str(value)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _step_key(step: dict[str, Any], index: int) -> tuple[Any, ...]:
    return (
        step.get("sequence"),
        step.get("id"),
        step.get("kind"),
        step.get("created_at"),
        index,
    )


class SwissTerminal:
    """Warehouse-inspired Swiss terminal presentation layer.

    The palette follows the Warehouse 2.0 paper/ink/red discipline. This class
    never grants authority or mutates operations; it renders immutable runtime
    state and collects explicit user input.
    """

    def __init__(self, console: Console | None = None):
        self.console = console or Console(highlight=False, soft_wrap=True)

    @property
    def width(self) -> int:
        return max(64, min(self.console.width, 144))

    def clear(self) -> None:
        if self.console.is_terminal:
            self.console.clear()

    def masthead(
        self,
        *,
        mode: str = "SYSTEM",
        workspace: str | None = None,
        project: str | None = None,
        brain: str = "READY",
        control: str = "LOCAL / SECURE",
        version: str = "0.4",
    ) -> None:
        top = Table.grid(expand=True)
        top.add_column(ratio=1)
        top.add_column(justify="right")
        brand = Text()
        brand.append("LH", style=f"bold {RED}")
        brand.append("  /  ", style=INK_4)
        brand.append("LIGHTHOUSE OS", style=f"bold {PAPER}")
        folio = Text(f"FOLIO {version}  ·  AI OPERATING TERMINAL", style=INK_3)
        top.add_row(brand, folio)
        self.console.print(top)
        self.console.print(Rule(style=RED, characters="━"))

        status = Table.grid(expand=True, padding=(0, 2))
        for _ in range(4):
            status.add_column(ratio=1)
        values = (
            ("KERNEL", str(mode or "system").upper(), RED),
            ("WORKSPACE", self._compact_id(workspace or "LOCAL"), PAPER),
            ("BRAIN", str(brain or "ready").upper(), GREEN if brain == "READY" else AMBER),
            ("CONTROL", str(control or "local / secure").upper(), CYAN),
        )
        cells: list[Group] = []
        for label, value, color in values:
            cells.append(Group(Text(label, style=f"bold {INK_4}"), Text(value, style=f"bold {color}")))
        status.add_row(*cells)
        self.console.print(status)

        project_line = Text()
        project_line.append("PROJECT  ", style=f"bold {INK_4}")
        project_line.append(str(project or os.getcwd()), style=INK_2)
        self.console.print(project_line)
        self.console.print(Rule(style="grey23", characters="─"))

    @staticmethod
    def _compact_id(value: str, limit: int = 24) -> str:
        value = str(value or "")
        if len(value) <= limit:
            return value
        return value[:10] + "…" + value[-8:]

    def guide(self) -> None:
        table = Table.grid(expand=True, padding=(0, 2))
        table.add_column(ratio=1)
        table.add_column(ratio=1)
        table.add_row(
            Text("NATURAL LANGUAGE", style=f"bold {INK_4}"),
            Text("LOCAL CONTROL", style=f"bold {INK_4}"),
        )
        table.add_row(
            Text("Describe a goal; LightHouse plans and acts through governed capabilities.", style=INK_3),
            Text("/help  /status  /capabilities  /mode  /init  /clear  /exit", style=INK_3),
        )
        self.console.print(table)
        self.console.print()

    def help(self) -> None:
        self.section("CONTROL INDEX", "LOCAL / NEVER AUTO-RUNS")
        rows = (
            ("lh", "Open the Swiss interactive terminal"),
            ('lh "task"', "Run one governed natural-language task"),
            ("/capabilities [query]", "Search the current capability atlas"),
            ("/mode auto|system|data", "Change the active kernel profile"),
            ("/init [path]", "Bind a project directory as a confined workspace"),
            ("! <exact command>", "Pass an exact legacy lh command"),
            ("/doctor", "Verify control plane, model and workspace"),
            ("/clear", "Redraw the terminal grid"),
            ("/exit", "Close this terminal session"),
        )
        table = Table(box=None, expand=True, padding=(0, 2), show_header=False)
        table.add_column(style=f"bold {PAPER}", width=28)
        table.add_column(style=INK_3)
        for command, description in rows:
            table.add_row(Text(command), Text(description))
        self.console.print(table)
        self.console.print()

    def section(self, label: str, meta: str | None = None) -> None:
        line = Table.grid(expand=True)
        line.add_column()
        line.add_column(justify="right")
        line.add_row(Text(label.upper(), style=f"bold {PAPER}"), Text((meta or "").upper(), style=INK_4))
        self.console.print(line)
        self.console.print(Rule(style="grey23", characters="─"))

    def capabilities(self, items: Iterable[dict[str, Any]]) -> None:
        rows = list(items)
        self.section("CAPABILITY ATLAS", f"{len(rows):02d} VISIBLE")
        table = Table(
            box=box.MINIMAL,
            expand=True,
            header_style=f"bold {INK_4}",
            border_style="grey23",
            row_styles=[PAPER, INK_2],
            padding=(0, 1),
        )
        table.add_column("COMMAND", ratio=2)
        table.add_column("TOOL", ratio=3)
        table.add_column("KERNEL", width=9)
        table.add_column("RISK", width=9)
        table.add_column("WRITE", width=7, justify="center")
        for item in rows:
            risk = str(item.get("risk") or "low").upper()
            risk_style = RED if risk in {"HIGH", "CRITICAL"} else AMBER if risk == "NORMAL" else INK_3
            table.add_row(
                _text(item.get("command"), PAPER),
                _text(item.get("tool_name"), INK_2),
                _text(str(item.get("kernel") or "").upper(), CYAN),
                _text(risk, risk_style),
                _text("W" if item.get("writes") else "—", RED if item.get("writes") else INK_4),
            )
        self.console.print(table)
        self.console.print()

    def task_banner(self, task: str) -> None:
        self.section("RUN REQUEST", "LIGHTHOUSE BRAIN")
        number = Text("01", style=f"bold {RED}")
        body = Text(str(task), style=PAPER)
        grid = Table.grid(expand=True, padding=(0, 2))
        grid.add_column(width=4)
        grid.add_column(ratio=1)
        grid.add_row(number, body)
        self.console.print(grid)
        self.console.print()

    def busy(self, label: str):
        if not self.console.is_terminal:
            return nullcontext()
        return self.console.status(
            Text(label.upper(), style=f"bold {PAPER}"),
            spinner="dots12",
            spinner_style=RED,
            refresh_per_second=12,
        )

    def render_run(
        self,
        snapshot: dict[str, Any],
        *,
        seen: set[tuple[Any, ...]] | None = None,
    ) -> set[tuple[Any, ...]]:
        seen = set() if seen is None else set(seen)
        run = snapshot.get("run") or {}
        steps = snapshot.get("steps") or []
        new_steps: list[tuple[int, dict[str, Any], tuple[Any, ...]]] = []
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            key = _step_key(step, index)
            if key not in seen:
                new_steps.append((index, step, key))

        if new_steps:
            run_id = self._compact_id(str(run.get("id") or ""), 28)
            status = str(run.get("status") or "running").upper()
            self.section(f"RUN / {run_id}", status)
            timeline = Table.grid(expand=True, padding=(0, 1))
            timeline.add_column(width=4, justify="right")
            timeline.add_column(width=12)
            timeline.add_column(ratio=1)
            for index, step, key in new_steps:
                kind = str(step.get("kind") or "event")
                label, number, color = _STAGE.get(kind, (kind.upper()[:11], "·", INK_3))
                timeline.add_row(
                    Text(number, style=f"bold {color}"),
                    Text(label, style=f"bold {color}"),
                    Text(self._step_summary(step), style=INK_2),
                )
                seen.add(key)
            self.console.print(timeline)
            self.console.print()
        return seen

    def _step_summary(self, step: dict[str, Any]) -> str:
        kind = str(step.get("kind") or "")
        payload = step.get("payload") if isinstance(step.get("payload"), dict) else step
        if kind == "run_created":
            return _short(payload.get("task"))
        if kind == "project_context":
            result = payload.get("result") or payload.get("context") or payload
            return "Project context indexed · " + _short(result, 140)
        if kind == "decision":
            decision_kind = str(payload.get("kind") or "decision").upper()
            target = payload.get("capability") or payload.get("message") or payload.get("reason")
            reason = payload.get("reason")
            return f"{decision_kind} · {_short(target, 115)}" + (f" · {_short(reason, 70)}" if reason and reason != target else "")
        if kind == "operation_dispatched":
            return f"{payload.get('capability') or 'operation'} · {str(payload.get('status') or 'created').upper()} · {self._compact_id(str(payload.get('operation_id') or ''), 26)}"
        if kind == "auto_confirmation":
            return f"Explicit confirmation applied · {self._compact_id(str(payload.get('operation_id') or ''), 26)}"
        if kind == "observation":
            receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
            operation = payload.get("operation") if isinstance(payload.get("operation"), dict) else {}
            ok = receipt.get("ok")
            state = "RECEIPT OK" if ok is True else "RECEIPT FAILED" if ok is False else str(operation.get("status") or "observed").upper()
            result = receipt.get("result") if receipt else payload.get("result")
            return f"{state} · {_short(result, 145)}"
        if kind in {"run_completed", "run_failed", "input_required", "user_input"}:
            return _short(payload.get("message") or payload.get("reason") or payload)
        if kind in {"protocol_error", "provider_error", "tool_rejected"}:
            return _short(payload.get("error") or payload)
        return _short(payload)

    def confirmation(self, pending: dict[str, Any]) -> None:
        operation = pending.get("operation") if isinstance(pending.get("operation"), dict) else pending
        envelope = operation.get("envelope") if isinstance(operation.get("envelope"), dict) else {}
        meta = Table.grid(expand=True, padding=(0, 2))
        meta.add_column(width=14, style=f"bold {INK_4}")
        meta.add_column(ratio=1, style=PAPER)
        meta.add_row("CAPABILITY", _text(operation.get("capability"), PAPER))
        meta.add_row("OPERATION", _text(operation.get("id"), INK_2))
        meta.add_row("KERNEL", _text(str(operation.get("kernel") or "").upper(), CYAN))
        meta.add_row("TARGET", _text(operation.get("target_id"), INK_2))
        arguments = envelope.get("arguments") if isinstance(envelope, dict) else None
        contents: list[Any] = [meta]
        if arguments:
            contents.extend([Text(""), Text("FROZEN ARGUMENTS", style=f"bold {INK_4}"), JSON.from_data(arguments)])
        self.console.print(
            Panel(
                Group(*contents),
                title=Text("CONFIRM / FROZEN OPERATION", style=f"bold {RED}"),
                subtitle=Text("NO WRITE HAS OCCURRED", style=INK_4),
                border_style=RED,
                box=box.SQUARE,
                padding=(1, 2),
            )
        )

    def confirm(self) -> bool:
        return Confirm.ask(Text("AUTHORIZE THIS EXACT OPERATION", style=f"bold {PAPER}"), default=False, console=self.console)

    def input_required(self, message: str | None = None) -> str:
        if message:
            self.notice("INPUT REQUIRED", message, tone="amber")
        return self.console.input(Text("LH / INPUT  › ", style=f"bold {AMBER}")).strip()

    def final(self, snapshot: dict[str, Any]) -> None:
        run = snapshot.get("run") or {}
        status = str(run.get("status") or "unknown").upper()
        message = str(run.get("final_message") or "Run state persisted.")
        tone = GREEN if status == "SUCCEEDED" else RED if status == "FAILED" else AMBER
        self.console.print(
            Panel(
                Text(message, style=PAPER),
                title=Text(f"{status} / RECEIPT-BACKED", style=f"bold {tone}"),
                border_style=tone,
                box=box.SQUARE,
                padding=(1, 2),
            )
        )
        self.console.print()

    def receipt(self, receipt: dict[str, Any]) -> None:
        ok = receipt.get("ok")
        tone = GREEN if ok else RED
        result = receipt.get("result")
        body: Any = JSON.from_data(result) if isinstance(result, (dict, list)) else Text(str(result or "—"), style=PAPER)
        self.console.print(
            Panel(
                body,
                title=Text("RECEIPT / VERIFIED" if ok else "RECEIPT / FAILED", style=f"bold {tone}"),
                subtitle=Text(str(receipt.get("result_hash") or "")[:20], style=INK_4),
                border_style=tone,
                box=box.SQUARE,
            )
        )

    def doctor(self, checks: dict[str, Any]) -> None:
        self.section("SYSTEM DIAGNOSTIC", "LOCAL CONTROL PLANE")
        table = Table(box=box.MINIMAL, expand=True, padding=(0, 1), header_style=f"bold {INK_4}")
        table.add_column("CHECK", ratio=1)
        table.add_column("STATE", width=12)
        table.add_column("DETAIL", ratio=3)
        for name, value in checks.items():
            if isinstance(value, bool):
                ok, detail = value, "READY" if value else "MISSING"
            elif isinstance(value, dict):
                ok = value.get("status") == "ok" or value.get("ok") is True
                detail = _short(value)
            else:
                ok = value is not None and value != "" and value is not False
                detail = _short(value)
            table.add_row(
                Text(str(name).replace("_", " ").upper(), style=PAPER),
                Text("READY" if ok else "CHECK", style=f"bold {GREEN if ok else AMBER}"),
                Text(detail, style=INK_3),
            )
        self.console.print(table)
        self.console.print()

    def notice(self, label: str, message: str, *, tone: str = "neutral") -> None:
        color = {"red": RED, "green": GREEN, "amber": AMBER, "cyan": CYAN}.get(tone, INK_3)
        self.console.print(Panel(Text(str(message), style=PAPER), title=Text(label.upper(), style=f"bold {color}"), border_style=color, box=box.SQUARE))

    def error(self, message: str) -> None:
        self.notice("ERROR", message, tone="red")

    def session(self, history_path: Path):
        if PromptSession is None or not self.console.is_terminal:
            return None
        history_path.parent.mkdir(parents=True, exist_ok=True)
        completer = WordCompleter(list(_LOCAL_COMMANDS), sentence=True, ignore_case=True)
        style = Style.from_dict(
            {
                "brand": "#e44535 bold",
                "mode": "#f2efe8 bold",
                "path": "#777777",
                "prompt": "#f2efe8 bold",
                "bottom-toolbar": "bg:#1f1f1f #9a9a9a",
            }
        )
        return PromptSession(
            history=FileHistory(str(history_path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=completer,
            complete_while_typing=False,
            style=style,
        )

    def prompt(self, *, mode: str, project: str, session=None) -> str:
        project_name = Path(project).name or project
        if session is not None:
            message = HTML(
                f"<brand>LH</brand><path> / </path><mode>{str(mode).upper()}</mode><path> / {self._escape_html(project_name)}</path><prompt>  ›  </prompt>"
            )
            toolbar = HTML("<b> /help </b> control index   <b>↑↓</b> history   <b>tab</b> complete   <b>^C</b> exit")
            return session.prompt(message, bottom_toolbar=toolbar).strip()
        return self.console.input(Text(f"LH / {str(mode).upper()} / {project_name}  ›  ", style=f"bold {PAPER}")).strip()

    @staticmethod
    def _escape_html(value: str) -> str:
        return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def terminal_size_warning(self) -> None:
        columns = shutil.get_terminal_size((100, 30)).columns
        if columns < 72:
            self.notice("DISPLAY", "For the full Swiss grid, widen the terminal to at least 72 columns.", tone="amber")
