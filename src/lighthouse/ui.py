from __future__ import annotations

import os
from typing import Iterable

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:  # pragma: no cover
    Console = None


class SwissTerminal:
    """Minimal Swiss-style command surface for the LightHouse terminal.

    The UI is intentionally separated from execution. OperationKernel remains
    the source of truth; this layer only presents status, events and prompts.
    """

    def __init__(self):
        self.console = Console() if Console else None

    def header(self, *, mode: str = "AUTO", workspace: str | None = None, project: str | None = None):
        if not self.console:
            return
        title = Text("LIGHTHOUSE OS", style="bold")
        table = Table.grid(padding=(0, 2))
        table.add_row("MODE", mode)
        table.add_row("WORKSPACE", workspace or "local")
        table.add_row("PROJECT", project or os.getcwd())
        self.console.print(Panel(table, title=title, border_style="white"))

    def welcome(self):
        if not self.console:
            print("LIGHTHOUSE OS")
            return
        self.console.print(Panel(
            "AI OPERATING TERMINAL\n\n"
            "Plan  →  Execute  →  Observe  →  Verify\n"
            "PostgreSQL Kernel · System Kernel",
            title="LIGHTHOUSE",
            border_style="white",
        ))

    def capabilities(self, items: Iterable[dict]):
        if not self.console:
            return
        table = Table(title="CAPABILITY ATLAS", border_style="white")
        table.add_column("NAME")
        table.add_column("KERNEL")
        table.add_column("RISK")
        for item in items:
            table.add_row(
                str(item.get("tool_name", "")),
                str(item.get("kernel", "")),
                str(item.get("risk", "")),
            )
        self.console.print(table)

    def task_state(self, status: str, message: str = ""):
        if not self.console:
            print(status, message)
            return
        self.console.print(Panel(message or status, title=status.upper(), border_style="white"))
