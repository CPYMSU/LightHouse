from __future__ import annotations

from typing import Any

from rich import box
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

from .ui import AMBER, CYAN, GREEN, INK_2, INK_3, INK_4, PAPER, RED, SwissTerminal, _message_text, _short


_ACTIVE = {"leased", "running", "waiting_dependency", "waiting_confirmation"}
_TERMINAL = {"succeeded", "failed", "cancelled", "superseded"}


class ObservatoryTerminal(SwissTerminal):
    """Swiss terminal with observable Agents, Tokens and action-time permission."""

    def __init__(self, console=None):
        super().__init__(console=console)
        self._agent_fingerprints: set[tuple[Any, ...]] = set()
        self._usage_fingerprint: tuple[Any, ...] | None = None

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
        seen = super().render_run(snapshot, seen=seen)
        self._render_agents(snapshot)
        self._render_usage(snapshot)
        return seen

    def _render_agents(self, snapshot: dict[str, Any]) -> None:
        observatory = snapshot.get("agent_observatory")
        if not isinstance(observatory, dict):
            return
        items = observatory.get("items") or []
        changed = []
        for item in items:
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
            f"{int(observatory.get('active') or 0)} ACTIVE / {int(observatory.get('total') or 0)} TOTAL",
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
            status = str(item.get("status") or "queued").upper()
            color = GREEN if str(item.get("status")) in _TERMINAL else AMBER if str(item.get("status")) in _ACTIVE else CYAN
            progress = float(item.get("progress") or 0)
            state = status + (f" {progress * 100:.0f}%" if progress > 0 else "")
            table.add_row(
                Text(self._compact_id(str(item.get("agent_id") or item.get("id") or ""), 20), style=INK_2),
                Text(str(item.get("role") or "specialist"), style=PAPER),
                Text(state, style=f"bold {color}"),
                _message_text(item.get("display_summary") or item.get("goal") or "—", INK_3),
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
        line.append(
            f"{self._compact_number(turn.get('total_tokens'))} TURN",
            style=f"bold {CYAN}",
        )
        line.append("  /  ", style=INK_4)
        line.append(
            f"{self._compact_number(conversation.get('total_tokens'))} CONVERSATION",
            style=INK_2,
        )
        line.append(
            f"   IN {self._compact_number(turn.get('input_tokens'))}"
            f" · OUT {self._compact_number(turn.get('output_tokens'))}"
            f" · CACHED {self._compact_number(turn.get('cached_input_tokens'))}{estimated}",
            style=INK_3,
        )
        self.console.print(line)
        self.console.print()

    def agents(self, payload: dict[str, Any]) -> None:
        self._agent_fingerprints.clear()
        self._render_agents({
            "agent_observatory": payload.get("observatory") or payload,
            "coordination_advice": payload.get("coordination_advice") or {},
        })

    def tokens(self, payload: dict[str, Any]) -> None:
        self._usage_fingerprint = None
        if "turn" in payload:
            self._render_usage({"token_usage": payload})
            return
        self.section("TOKEN USAGE", "MODEL CALL RECEIPTS")
        table = Table(box=box.MINIMAL, expand=True, padding=(0, 1), header_style=f"bold {INK_4}")
        table.add_column("CALLS", width=8)
        table.add_column("INPUT", width=12)
        table.add_column("OUTPUT", width=12)
        table.add_column("CACHED", width=12)
        table.add_column("TOTAL", width=12)
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
        body = [
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
        agents = snapshot.get("agent_observatory") or {}
        usage = snapshot.get("token_usage") or {}
        turn = usage.get("turn") if isinstance(usage, dict) else {}
        if agents or turn:
            body.extend([
                Text(""),
                Text(
                    f"AGENTS {int(agents.get('total') or 0)} USED · "
                    f"{int(agents.get('completed') or 0)} TERMINAL    "
                    f"TOKENS {self._compact_number((turn or {}).get('total_tokens'))} THIS TURN",
                    style=INK_2,
                ),
            ])
        self.console.print(
            Panel(
                *body,
                title=Text(f"{status} / RECEIPT-BACKED", style=f"bold {tone}"),
                border_style=tone,
                box=box.SQUARE,
                padding=(1, 2),
            )
        )
        self.console.print()

    @staticmethod
    def _compact_number(value: Any) -> str:
        number = int(value or 0)
        if number >= 1_000_000:
            return f"{number / 1_000_000:.1f}M"
        if number >= 1_000:
            return f"{number / 1_000:.1f}K"
        return str(number)
