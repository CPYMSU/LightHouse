from __future__ import annotations

from pathlib import Path
import getpass
import os
import shlex
import sys
from typing import Any

from rich.prompt import Confirm
from rich.text import Text

from . import cli as legacy
from . import terminal as base
from . import terminal_v2 as durable
from .ui import SwissTerminal


AUTO_MODE_KEY = "auto_mode"


def auto_mode_enabled(config: dict[str, Any]) -> bool:
    """Return the interactive Auto Mode preference.

    Auto Mode is enabled by default after the 0.9 upgrade, but it never grants
    silent authority by itself. Every new Run still requires one explicit
    scope-level authorization before `auto_confirm` is sent to the server.
    """

    return bool(config.get(AUTO_MODE_KEY, True))


def set_auto_mode(
    config: dict[str, Any],
    enabled: bool,
    *,
    ui: SwissTerminal | None = None,
) -> bool:
    config[AUTO_MODE_KEY] = bool(enabled)
    base._save(config)
    if ui is not None:
        ui.notice(
            "AUTO MODE",
            (
                "Enabled. Each new Run asks once for scoped authorization, then "
                "continues without repeated operation confirmations."
                if enabled
                else "Disabled. Every operation that requires confirmation will ask separately."
            ),
            tone="green" if enabled else "cyan",
        )
    return bool(enabled)


def _ask_auto_mode(
    ui: SwissTerminal,
    *,
    task: str,
    config: dict[str, Any],
) -> bool:
    """Ask for one explicit authorization covering only the next durable Run."""

    workspace = str(
        config.get("workspace_name")
        or config.get("workspace")
        or "local"
    )
    project = str(config.get("project_path") or os.getcwd())
    ui.notice(
        "AUTO MODE / RUN AUTHORIZATION",
        (
            "Authorize this task to continue through all governed operations without "
            "asking again during the same Run.\n\n"
            f"Task: {task}\n"
            f"Workspace: {workspace}\n"
            f"Project: {project}\n\n"
            "The authorization ends when this Run succeeds, fails, is cancelled, or "
            "waits for new user input. Every operation remains immutable and Receipt-backed."
        ),
        tone="amber",
    )
    if not ui.console.is_terminal:
        return False
    return Confirm.ask(
        Text("AUTHORIZE AUTO MODE FOR THIS RUN", style="bold bright_white"),
        default=False,
        console=ui.console,
    )


def run_task(
    task: str,
    *,
    auto_confirm: bool = False,
    auto_mode: bool | None = None,
    client: legacy.Client | None = None,
    config: dict[str, Any] | None = None,
    ui: SwissTerminal | None = None,
    new_conversation: bool = False,
) -> int:
    task = str(task or "").strip()
    if not task:
        raise legacy.CLIError("task cannot be empty")
    ui = ui or SwissTerminal()
    if config is None:
        _path, config = base._config()
    client = client or base._client(config)
    with ui.busy("WORKSPACE / RESOLVE THREE KERNELS"):
        base.ensure_workspace(client, config, Path.cwd().resolve())
    durable._scan_memory(client, config, ui)
    actor = str(config.get("actor") or getpass.getuser())
    ui.task_banner(task)

    requested_auto = (
        auto_mode_enabled(config)
        if auto_mode is None
        else bool(auto_mode)
    )
    run_auto_confirm = bool(auto_confirm)
    if requested_auto and not run_auto_confirm:
        run_auto_confirm = _ask_auto_mode(ui, task=task, config=config)
        if run_auto_confirm:
            ui.notice(
                "AUTO MODE ARMED",
                "One-time authorization accepted. LightHouse will continue until this Run reaches a terminal or input-waiting state.",
                tone="green",
            )
        else:
            ui.notice(
                "MANUAL CONFIRMATION",
                "Auto Mode was not authorized. This Run will request confirmation operation by operation.",
                tone="cyan",
            )

    with ui.busy("BRAIN / START DURABLE RUN"):
        snapshot = client.request(
            "POST",
            "/v1/agent/runs",
            {
                "task": task,
                "workspace_id": config["workspace"],
                "actor": actor,
                "mode": config.get("mode") or "auto",
                "max_steps": 24,
                "auto_confirm": run_auto_confirm,
                "conversation_id": (
                    None if new_conversation else config.get("conversation_id")
                ),
                "new_conversation": bool(new_conversation),
            },
        )
    conversation = (
        snapshot.get("conversation")
        if isinstance(snapshot.get("conversation"), dict)
        else {}
    )
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
        base._save(config)
    snapshot = durable._drive_run(client, snapshot, actor=actor, ui=ui)
    conversation = (
        snapshot.get("conversation")
        if isinstance(snapshot.get("conversation"), dict)
        else {}
    )
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
        base._save(config)
    return 0


def _auto_command(
    argument: str,
    *,
    config: dict[str, Any],
    ui: SwissTerminal,
) -> None:
    requested = str(argument or "status").strip().lower()
    if requested in {"", "status"}:
        ui.notice(
            "AUTO MODE",
            (
                "ON — one scoped confirmation per Run"
                if auto_mode_enabled(config)
                else "OFF — confirm each governed operation"
            ),
            tone="green" if auto_mode_enabled(config) else "cyan",
        )
        return
    if requested in {"on", "enable", "enabled", "1", "true"}:
        set_auto_mode(config, True, ui=ui)
        return
    if requested in {"off", "disable", "disabled", "0", "false"}:
        set_auto_mode(config, False, ui=ui)
        return
    ui.notice(
        "AUTO MODE",
        "Use /auto on, /auto off or /auto status.",
        tone="amber",
    )


def interactive() -> int:
    ui = SwissTerminal()
    _path, config = base._config()
    if AUTO_MODE_KEY not in config:
        config[AUTO_MODE_KEY] = True
        base._save(config)
    client = base._client(config)
    current_project = Path.cwd().resolve()
    with ui.busy("WORKSPACE / BIND SYSTEM + DESKTOP"):
        base.ensure_workspace(client, config, current_project)
    durable._scan_memory(client, config, ui)
    base._redraw(ui, config)
    _auto_command("status", config=config, ui=ui)
    session = ui.session(Path.home() / ".lighthouse" / "history")
    while True:
        try:
            line = ui.prompt(
                mode=str(config.get("mode") or "auto"),
                project=str(config.get("project_path") or current_project),
                session=session,
            )
        except (EOFError, KeyboardInterrupt):
            ui.console.print()
            return 0
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            return 0
        if line == "/clear":
            base._redraw(ui, config)
        elif line == "/help":
            ui.help()
            ui.notice(
                "AUTO MODE",
                "/auto on — one scoped confirmation per Run\n/auto off — confirm every governed operation\n/auto status — show the current setting",
                tone="cyan",
            )
        elif line == "/new":
            config.pop("conversation_id", None)
            base._save(config)
            ui.notice(
                "NEW CONVERSATION",
                "The next task starts a new conversation while retaining indexed long-term memory.",
                tone="cyan",
            )
        elif line == "/reindex":
            durable._scan_memory(client, config, ui, force=True)
        elif line == "/status":
            base._redraw(ui, config)
            _auto_command("status", config=config, ui=ui)
            base.doctor(ui=ui)
        elif line.startswith("/auto"):
            _auto_command(
                line[len("/auto"):].strip(),
                config=config,
                ui=ui,
            )
        elif line.startswith("/capabilities"):
            base._capability_view(
                client,
                config,
                ui,
                line[len("/capabilities"):].strip(),
            )
        elif line.startswith("/mode"):
            requested = line[len("/mode"):].strip().lower()
            if requested not in {"auto", "system", "data", "desktop"}:
                ui.notice(
                    "MODE",
                    "Use /mode auto, /mode system, /mode data or /mode desktop.",
                    tone="amber",
                )
            else:
                config["mode"] = requested
                base._save(config)
                ui.notice(
                    "KERNEL PROFILE",
                    f"Active mode changed to {requested.upper()}.",
                    tone="cyan",
                )
        elif line.startswith("/init"):
            base.init_project(
                line[len("/init"):].strip() or os.getcwd(),
                ui=ui,
            )
            _path, config = base._config()
            client = base._client(config)
            base._redraw(ui, config)
        elif line == "/doctor":
            base.doctor(ui=ui)
        elif line == "/login":
            base.login(ui=ui)
            _path, config = base._config()
            client = base._client(config)
        elif line.startswith("/receipt "):
            operation_id = line.split(maxsplit=1)[1]
            with ui.busy("RECEIPT / LOAD"):
                receipt = client.request(
                    "GET",
                    f"/v1/operations/{operation_id}/receipt",
                )
            ui.receipt(receipt)
        elif line.startswith("!"):
            command = line[1:].strip()
            if not command:
                ui.notice(
                    "EXACT COMMAND",
                    "Prefix a complete lh command with !",
                    tone="amber",
                )
                continue
            try:
                argv = shlex.split(command)
            except ValueError as exc:
                ui.error(str(exc))
                continue
            ui.section("EXACT COMMAND", command)
            legacy.main(argv)
        else:
            run_task(line, client=client, config=config, ui=ui)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    durable_base_main = base.main

    # Keep the durable 0.8 execution path while replacing its run entry and
    # interactive shell with the one-confirmation 0.9 Auto Mode surface.
    base._scan_memory = durable._scan_memory
    base._drive_run = durable._drive_run
    base.run_task = run_task
    base.interactive = interactive

    if argv and argv[0] == "auto":
        ui = SwissTerminal()
        _path, config = base._config()
        _auto_command(
            " ".join(argv[1:]) if len(argv) > 1 else "status",
            config=config,
            ui=ui,
        )
        return 0
    return durable_base_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
