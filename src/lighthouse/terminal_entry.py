from __future__ import annotations

import os
from typing import Any

from . import __version__
from . import terminal as base
from . import terminal_v3  # compatibility marker for 1.0 installer contracts
from . import terminal_v4
from .conversation_control import install_terminal_hooks
from .ui import SwissTerminal


def _redraw(
    ui: SwissTerminal,
    config: dict[str, Any],
    *,
    brain: str = "READY",
) -> None:
    """Render the terminal using the installed package version."""
    ui.clear()
    auto_available = bool(config.get("auto_mode", True))
    ui.masthead(
        mode=str(config.get("mode") or "auto"),
        workspace=str(
            config.get("workspace_name")
            or config.get("workspace")
            or "local"
        ),
        project=str(config.get("project_path") or os.getcwd()),
        brain=brain,
        control=(
            "ASK ON ACTION / AUTO READY"
            if auto_available
            else "ASK ON ACTION / ONCE"
        ),
        version=__version__,
    )
    ui.guide()
    ui.terminal_size_warning()


def main(argv: list[str] | None = None) -> int:
    base._redraw = _redraw
    install_terminal_hooks(base, terminal_v4)
    return terminal_v4.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
