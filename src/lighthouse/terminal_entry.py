from __future__ import annotations

import os
from typing import Any

from . import __version__
from . import terminal as base
from . import terminal_v3
from .ui import SwissTerminal


def _redraw(
    ui: SwissTerminal,
    config: dict[str, Any],
    *,
    brain: str = "READY",
) -> None:
    """Render the terminal using the installed package version."""
    ui.clear()
    auto_mode = bool(config.get("auto_mode", True))
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
            "AUTO / ONE CONFIRM"
            if auto_mode
            else "MANUAL / EXACT CONFIRM"
        ),
        version=__version__,
    )
    ui.guide()
    ui.terminal_size_warning()


def main(argv: list[str] | None = None) -> int:
    # terminal_v3 keeps the durable 0.8 execution path and adds the 0.9
    # one-confirmation Auto Mode surface.
    base._redraw = _redraw
    return terminal_v3.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
