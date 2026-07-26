from __future__ import annotations

import json
from typing import Any

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .ui import (
    AMBER,
    CYAN,
    GREEN,
    INK_2,
    INK_3,
    INK_4,
    PAPER,
    RED,
    SwissTerminal,
    _message_text,
    _short,
    _step_key,
)


_ACTIVE = {"leased", "running", "waiting_dependency", "waiting_confirmation"}
_TERMINAL = {"succeeded", "failed", "cancelled", "superseded"}
_OBSERVE_MODES = {"off", "focus", "balanced", "verbose"}
_PHASE_STYLE = {
    "understanding": CYAN,
    "investigating": CYAN,
    "finding": AMBER,
    "decision": PAPER,
    "implementing": RED,
    "verifying": GREEN,
    "recovering": RED,
    "completing": GREEN,
}


class ObservatoryTerminal(SwissTerminal):
    """Swiss terminal with cognitive continuity, Agents and Token receipts."""

    def __init__(self, console=None, *, observe_mode: str = "balanced"):
        super().__init__(console=console)
        self._agent_fingerprints: set[tuple[Any, ...]] = set()
        self._usage_fingerprint: tuple[Any, ...] | None = None
        self._work_fingerprint: str | None = None
        self._status_fingerprint: tuple[Any, ...] | None = None
        self.observe_mode = "balanced"
        self.set_observe_mode(observe_mode, announce=False)

    def set_observe_mode(self, mode: str, *, announce: bool = True) -> str:
        requested = str(mode or "balanced").strip().lower()
        if requested not in _OBSERVE_MODES:
            raise ValueError("observe mode must be off, focus, balanced or verbose")
        self.observe_mode = requested
        if announce:
            descriptions = {
                "off": "Only permission, failure and final result surfaces remain visible.",
                "focus": "Show major direction changes, code changes, failures and verification.",
                "balanced": "Show meaningful AI understanding, engineering activity and progress.",
                "verbose": "Show all cognitive updates, tool activity, Agents and Token receipts.",
            }
            self.notice("OBSERVE MODE", f"{requested.upper()} — {descriptions[requested]}", tone="cyan")
        return requested

    def permission_choice(self, *, auto_available: bool = True) -> str:
        if not self.console.is_terminal:
            return "deny"
        choices = ["once", "deny"]
        message = "[once] Allow once  [deny] Deny"
        if auto_available:
            choices.insert(1, "auto")
            message = "[once] Allow once  [auto] Auto-approve this Run  [deny] Deny"
        self.console.print(Text(message, style=f"bold {PAPER}"))
        return Prompt.ask(
            Text("PERMISSION", style=f"bold {AMBER}"),
            choices=choices,
            default="once",
            console=self.console,
        )

    def render_run(
        self,
        snapshot: dict[str, Any],
        *,
        seen: set[tuple[Any, ...]] | None = None,
    ) -> set[tuple[Any, ...]]:
        seen = set() if seen is None else set(seen)
        new_steps: list[tuple[dict[str, Any], tuple[Any, ...]]] = []
        for index, step in enumerate(snapshot.get("steps") or []):
            if not isinstance(step, dict):
                continue
            key = _step_key(step, index)
            if key not in seen:
                new_steps.append((step, key))

        if new_steps:
            new_sequences = {int(step.get("sequence") or 0) for step, _key in new_steps}
            self._render_cognition(snapshot, new_sequences)
            self._render_activity(snapshot, new_sequences)
            for _step, key in new_steps:
                seen.add(key)

        self._render_work_state(snapshot)
        if self.observe_mode != "off":
            self._render_agents(snapshot)
        if self.observe_mode in {"balanced", "verbose"}:
            self._render_usage(snapshot)
        self._render_status_line(snapshot)
        return seen

    def _visible_update(self, item: dict[str, Any]) -> bool:
        importance = str(item.get("importance") or "normal")
        visibility = str(item.get("visibility") or "balanced")
        if self.observe_mode == "verbose":
            return True
        if self.observe_mode == "balanced":
            return visibility in {"focus", "balanced"} or importance in {"important", "critical"}
        if self.observe_mode == "focus":
            return visibility == "focus" or importance == "critical"
        return importance == "critical"

    def _render_cognition(self, snapshot: dict[str, Any], sequences: set[int]) -> None:
        observer = snapshot.get("cognitive_observer")
        if not isinstance(observer, dict):
            return
        updates = [
            item
            for item in observer.get("timeline") or []
            if isinstance(item, dict)
            and int(item.get("sequence") or 0) in sequences
            and self._visible_update(item)
        ]
        for item in updates:
            phase = str(item.get("phase") or "investigating").lower()
            color = _PHASE_STYLE.get(phase, INK_3)
            heading = Text()
            heading.append(self._phase_mark(phase) + " ", style=f"bold {color}")
            heading.append(phase.upper() + "  ", style=f"bold {color}")
            heading.append(str(item.get("title") or "AI update"), style=f"bold {PAPER}")
            self.console.print(heading)
            summary = str(item.get("summary") or "").strip()
            if summary:
                self.console.print(_message_text("  " + summary, INK_2))
            for detail in item.get("details") or []:
                self.console.print(_message_text("  • " + str(detail), INK_3))
            evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else []
            if evidence and self.observe_mode in {"balanced", "verbose"}:
                labels = []
                for value in evidence[:6]:
                    if isinstance(value, dict):
                        label = value.get("path") or value.get("symbol") or value.get("label") or value.get("type")
                        if label:
                            labels.append(str(label))
                if labels:
                    self.console.print(Text("  EVIDENCE  " + " · ".join(labels), style=INK_4))
            self.console.print()

    def _render_activity(self, snapshot: dict[str, Any], sequences: set[int]) -> None:
        observer = snapshot.get("cognitive_observer")
        if not isinstance(observer, dict):
            return
        items = [
            item
            for item in observer.get("activity") or []
            if isinstance(item, dict)
            and int(item.get("sequence") or 0) in sequences
            and self._visible_activity(item)
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
            color = RED if status == "failed" else GREEN if status == "succeeded" else CYAN
            table.add_row(
                Text(label, style=f"bold {color}"),
                _message_text(item.get("summary") or item.get("capability") or "—", INK_2),
                Text(status.upper(), style=INK_4),
            )
        self.console.print(table)
        self.console.print()

    def _visible_activity(self, item: dict[str, Any]) -> bool:
        status = str(item.get("status") or "")
        importance = str(item.get("importance") or "normal")
        label = str(item.get("label") or "")
        if status == "failed":
            return True
        if self.observe_mode == "verbose":
            return True
        if self.observe_mode == "balanced":
            return importance in {"important", "critical"} or label in {"EDIT", "TEST", "DIFF", "DATABASE", "SERVICE"}
        if self.observe_mode == "focus":
            return importance == "critical" or label in {"EDIT", "TEST", "DIFF"}
        return False

    def _render_work_state(self, snapshot: dict[str, Any]) -> None:
        if self.observe_mode not in {"balanced", "verbose"}:
            return
        observer = snapshot.get("cognitive_observer")
        state = observer.get("state") if isinstance(observer, dict) and isinstance(observer.get("state"), dict) else {}
        active = state.get("active_work") if isinstance(state.get("active_work"), dict) else {}
        work_items = state.get("work_items") if isinstance(state.get("work_items"), list) else []
        validation = state.get("validation") if isinstance(state.get("validation"), dict) else {}
        fingerprint = json.dumps(
            {
                "stage": active.get("stage"),
                "headline": active.get("headline"),
                "work_items": work_items,
                "files": active.get("changed_files"),
                "validation": validation,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        if fingerprint == self._work_fingerprint:
            return
        self._work_fingerprint = fingerprint
        if not active and not work_items:
            return
        stage = str(active.get("stage") or "working").upper()
        headline = str(active.get("headline") or "LightHouse is working")
        self.section("WORK STATE", f"{stage} / ESTIMATED")
        self.console.print(_message_text(headline, PAPER))
        if work_items:
            table = Table(box=box.MINIMAL, expand=True, padding=(0, 1), show_header=False)
            table.add_column(width=4)
            table.add_column(ratio=1, overflow="fold")
            table.add_column(width=14, justify="right")
            for item in work_items[-12:]:
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status") or "pending")
                mark = "✓" if status == "completed" else "●" if status in {"active", "in_progress"} else "○"
                color = GREEN if status == "completed" else AMBER if status in {"active", "in_progress"} else INK_4
                table.add_row(Text(mark, style=f"bold {color}"), _message_text(item.get("title") or item.get("name") or item.get("id"), INK_2), Text(status.upper(), style=color))
            self.console.print(table)
        files = list(active.get("changed_files") or [])
        if files:
            self.console.print(Text(f"FILES  {len(files)} CHANGED · " + " · ".join(str(item) for item in files[:5]), style=INK_3))
        if validation:
            self.console.print(
                Text(
                    f"VERIFY  {int(validation.get('passed') or 0)} PASS · "
                    f"{int(validation.get('failed') or 0)} FAIL · "
                    f"{int(validation.get('running') or 0)} RUNNING",
                    style=INK_3,
                )
            )
        self.console.print()

    def _render_status_line(self, snapshot: dict[str, Any]) -> None:
        if self.observe_mode == "off":
            return
        run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
        observer = snapshot.get("cognitive_observer") if isinstance(snapshot.get("cognitive_observer"), dict) else {}
        state = observer.get("state") if isinstance(observer.get("state"), dict) else {}
        active = state.get("active_work") if isinstance(state.get("active_work"), dict) else {}
        validation = state.get("validation") if isinstance(state.get("validation"), dict) else {}
        agents = snapshot.get("agent_observatory") if isinstance(snapshot.get("agent_observatory"), dict) else {}
        fingerprint = (
            run.get("status"),
            active.get("stage"),
            active.get("headline"),
            len(active.get("changed_files") or []),
            validation.get("passed"),
            validation.get("failed"),
            agents.get("active"),
            run.get("auto_confirm"),
        )
        if fingerprint == self._status_fingerprint:
            return
        self._status_fingerprint = fingerprint
        stage = str(active.get("stage") or run.get("status") or "working").upper()
        headline = _short(active.get("headline") or "LightHouse run active", 90)
        suffix = (
            f" · {len(active.get('changed_files') or [])} FILES"
            f" · {int(agents.get('active') or 0)} AGENTS"
            f" · {'AUTO' if run.get('auto_confirm') else 'GOVERNED'}"
        )
        line = Text()
        line.append("STATUS  ", style=f"bold {INK_4}")
        line.append(stage, style=f"bold {_PHASE_STYLE.get(str(active.get('stage') or ''), CYAN)}")
        line.append(" · " + headline + suffix, style=INK_3)
        self.console.print(line)
        self.console.print()

    def _render_agents(self, snapshot: dict[str, Any]) -> None:
        observatory = snapshot.get("agent_observatory")
        if not isinstance(observatory, dict):
            return
        changed = []
        for item in observatory.get("items") or []:
            if not isinstance(item, dict):
                continue
            fingerprint = (
                item.get("id"), item.get("status"), item.get("progress"),
                item.get("display_summary"), item.get("updated_at"),
            )
            if fingerprint not in self._agent_fingerprints:
                changed.append(item)
                self._agent_fingerprints.add(fingerprint)
        if not changed:
            return
        self.section(
            "AGENT FIELD",
            f"{int(observatory.get('active') or 0)} ACTIVE / "
            f"{int(observatory.get('total') or 0)} TOTAL",
        )
        table = Table(
            box=box.MINIMAL,
            expand=True,
            padding=(0, 1),
            header_style=f"bold {INK_4}",
        )
        table.add_column("AGENT", width=20)
        table.add_column("ROLE", width=20)
        table.add_column("STATE", width=20)
        table.add_column("WORK", ratio=1, overflow="fold")
        for item in changed[-30:]:
            raw_status = str(item.get("status") or "queued")
            status = raw_status.upper()
            color = GREEN if raw_status in _TERMINAL else AMBER if raw_status in _ACTIVE else CYAN
            progress = float(item.get("progress") or 0)
            state = status + (f" {progress * 100:.0f}%" if progress > 0 else "")
            table.add_row(
                Text(
                    self._compact_id(str(item.get("agent_id") or item.get("id") or ""), 20),
                    style=INK_2,
                ),
                Text(str(item.get("role") or "specialist"), style=PAPER),
                Text(state, style=f"bold {color}"),
                _message_text(
                    item.get("display_summary") or item.get("goal") or "—",
                    INK_3,
                ),
            )
        self.console.print(table)
        advice = snapshot.get("coordination_advice")
        if isinstance(advice, dict) and advice.get("recommended_strategy"):
            self.console.print(
                Text(
                    "BUS ADVICE  "
                    + str(advice.get("recommended_strategy")).upper()
                    + " · "
                    + _short(advice.get("reason"), 160)
                    + " · MAIN AI DECIDES",
                    style=INK_3,
                )
            )
        self.console.print()

    def _render_usage(self, snapshot: dict[str, Any]) -> None:
        usage = snapshot.get("token_usage")
        if not isinstance(usage, dict):
            return
        turn = usage.get("turn") if isinstance(usage.get("turn"), dict) else {}
        conversation = usage.get("conversation") if isinstance(usage.get("conversation"), dict) else {}
        fingerprint = (
            turn.get("calls"), turn.get("input_tokens"), turn.get("output_tokens"),
            turn.get("total_tokens"), conversation.get("total_tokens"),
        )
        if fingerprint == self._usage_fingerprint or not any(fingerprint):
            return
        self._usage_fingerprint = fingerprint
        estimated = " · CONTAINS ESTIMATES" if turn.get("contains_estimates") else ""
        line = Text()
        line.append("TOKENS  ", style=f"bold {INK_4}")
        line.append(f"{self._compact_number(turn.get('total_tokens'))} TURN", style=f"bold {CYAN}")
        line.append("  /  ", style=INK_4)
        line.append(f"{self._compact_number(conversation.get('total_tokens'))} CONVERSATION", style=INK_2)
        line.append(
            f"   IN {self._compact_number(turn.get('input_tokens'))}"
            f" · OUT {self._compact_number(turn.get('output_tokens'))}"
            f" · CACHED {self._compact_number(turn.get('cached_input_tokens'))}{estimated}",
            style=INK_3,
        )
        self.console.print(line)
        self.console.print()

    def cognition(self, payload: dict[str, Any]) -> None:
        observer = payload.get("observer") if isinstance(payload.get("observer"), dict) else payload
        state = observer.get("state") if isinstance(observer, dict) else {}
        previous_mode = self.observe_mode
        try:
            self.observe_mode = "balanced"
            self._work_fingerprint = None
            self._render_work_state({"cognitive_observer": {"state": state}})
        finally:
            self.observe_mode = previous_mode

    def agents(self, payload: dict[str, Any]) -> None:
        self._agent_fingerprints.clear()
        self._render_agents(
            {
                "agent_observatory": payload.get("observatory") or payload,
                "coordination_advice": payload.get("coordination_advice") or {},
            }
        )

    def tokens(self, payload: dict[str, Any]) -> None:
        self._usage_fingerprint = None
        if "turn" in payload:
            self._render_usage({"token_usage": payload})
            return
        self.section("TOKEN USAGE", "MODEL CALL RECEIPTS")
        table = Table(box=box.MINIMAL, expand=True, padding=(0, 1), header_style=f"bold {INK_4}")
        for label, width in (("CALLS", 8), ("INPUT", 12), ("OUTPUT", 12), ("CACHED", 12), ("TOTAL", 12)):
            table.add_column(label, width=width)
        table.add_column("QUALITY", ratio=1)
        table.add_row(
            str(payload.get("calls") or 0),
            self._compact_number(payload.get("input_tokens")),
            self._compact_number(payload.get("output_tokens")),
            self._compact_number(payload.get("cached_input_tokens")),
            self._compact_number(payload.get("total_tokens")),
            "CONTAINS ESTIMATES" if payload.get("contains_estimates") else "PROVIDER REPORTED",
        )
        self.console.print(table)
        self.console.print()

    def final(self, snapshot: dict[str, Any]) -> None:
        run = snapshot.get("run") or {}
        status = str(run.get("status") or "unknown").upper()
        message = str(run.get("final_message") or "Run state persisted.")
        warning = str(run.get("warning") or "").strip()
        tone = GREEN if status == "SUCCEEDED" else AMBER if status in {"COMPLETED_WITH_WARNING", "PARTIALLY_COMPLETED"} else RED if status == "FAILED" else AMBER
        body: list[Any] = [
            _message_text(message),
            Text(""),
            Text(
                f"EXECUTION {str(run.get('execution_status') or 'unknown').upper()}  ·  "
                f"RESPONSE {str(run.get('response_status') or 'unknown').upper()}  ·  "
                f"GOAL {str(run.get('goal_status') or 'unknown').upper()}",
                style=INK_3,
            ),
        ]
        if warning:
            body.extend([Text(""), Text("WARNING  " + warning, style=AMBER)])
        observer = snapshot.get("cognitive_observer") if isinstance(snapshot.get("cognitive_observer"), dict) else {}
        state = observer.get("state") if isinstance(observer.get("state"), dict) else {}
        active = state.get("active_work") if isinstance(state.get("active_work"), dict) else {}
        agents = snapshot.get("agent_observatory") or {}
        usage = snapshot.get("token_usage") or {}
        turn = usage.get("turn") if isinstance(usage, dict) else {}
        body.extend(
            [
                Text(""),
                Text(
                    f"FILES {len(active.get('changed_files') or [])} CHANGED    "
                    f"AGENTS {int(agents.get('total') or 0)} USED    "
                    f"TOKENS {self._compact_number((turn or {}).get('total_tokens'))} THIS TURN",
                    style=INK_2,
                ),
            ]
        )
        self.console.print(
            Panel(
                Group(*body),
                title=Text(f"{status} / RECEIPT-BACKED", style=f"bold {tone}"),
                border_style=tone,
                box=box.SQUARE,
                padding=(1, 2),
            )
        )
        self.console.print()

    @staticmethod
    def _phase_mark(phase: str) -> str:
        return {
            "understanding": "◆",
            "investigating": "→",
            "finding": "!",
            "decision": "◇",
            "implementing": "→",
            "verifying": "✓",
            "recovering": "×",
            "completing": "✓",
        }.get(phase, "·")

    @staticmethod
    def _compact_number(value: Any) -> str:
        number = int(value or 0)
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"
        if number >= 1_000:
            return f"{number / 1_000:.1f}K"
        return str(number)
