from __future__ import annotations

from pathlib import Path
import getpass
import os
import shlex
import sys
from typing import Any

from . import cli as legacy
from . import terminal as base
from . import terminal_v2 as durable
from .ui_v12 import ObservatoryTerminal


AUTO_MODE_KEY = "auto_mode"
OBSERVE_MODE_KEY = "observe_mode"
_OBSERVE_MODES = {"off", "focus", "balanced", "verbose"}


def auto_mode_enabled(config: dict[str, Any]) -> bool:
    """Whether action-time permission cards offer a Run-scoped Auto option."""
    return bool(config.get(AUTO_MODE_KEY, True))


def observe_mode(config: dict[str, Any]) -> str:
    value = str(config.get(OBSERVE_MODE_KEY) or "balanced").strip().lower()
    return value if value in _OBSERVE_MODES else "balanced"


def set_auto_mode(
    config: dict[str, Any],
    enabled: bool,
    *,
    ui: ObservatoryTerminal | None = None,
) -> bool:
    config[AUTO_MODE_KEY] = bool(enabled)
    base._save(config)
    if ui is not None:
        ui.notice(
            "AUTO MODE",
            (
                "Enabled. LightHouse stays conversational and offers Auto-approve this Run only when a governed action first needs permission."
                if enabled
                else "Disabled. Each governed operation asks for exact one-time permission."
            ),
            tone="green" if enabled else "cyan",
        )
    return bool(enabled)


def set_observe_mode(
    config: dict[str, Any],
    mode: str,
    *,
    ui: ObservatoryTerminal | None = None,
) -> str:
    requested = str(mode or "balanced").strip().lower()
    if requested not in _OBSERVE_MODES:
        raise ValueError("observe mode must be off, focus, balanced or verbose")
    config[OBSERVE_MODE_KEY] = requested
    base._save(config)
    if ui is not None:
        ui.set_observe_mode(requested)
    return requested


def _auto_command(
    argument: str,
    *,
    config: dict[str, Any],
    ui: ObservatoryTerminal,
) -> None:
    requested = str(argument or "status").strip().lower()
    if requested in {"", "status"}:
        ui.notice(
            "AUTO MODE",
            (
                "ON — Auto is offered only at the first governed action"
                if auto_mode_enabled(config)
                else "OFF — allow each governed operation once"
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
    ui.notice("AUTO MODE", "Use /auto on, /auto off or /auto status.", tone="amber")


def _observe_command(
    argument: str,
    *,
    config: dict[str, Any],
    ui: ObservatoryTerminal,
) -> None:
    requested = str(argument or "status").strip().lower()
    if requested in {"", "status"}:
        ui.notice(
            "OBSERVE MODE",
            f"{observe_mode(config).upper()} — use /observe off|focus|balanced|verbose",
            tone="cyan",
        )
        return
    try:
        set_observe_mode(config, requested, ui=ui)
    except ValueError as exc:
        ui.notice("OBSERVE MODE", str(exc), tone="amber")


def _steer_run(
    client: legacy.Client,
    config: dict[str, Any],
    ui: ObservatoryTerminal,
    *,
    run_id: str,
    message: str,
) -> dict[str, Any] | None:
    run_id = str(run_id or "").strip()
    message = str(message or "").strip()
    if not run_id:
        ui.notice("STEER", "No Run ID is available. Start a Run or provide one explicitly.", tone="amber")
        return None
    if not message:
        ui.notice("STEER", "Provide a concrete direction for the active Run.", tone="amber")
        return None
    actor = str(config.get("actor") or getpass.getuser())
    with ui.busy("COGNITIVE CONTINUITY / APPLY USER DIRECTION"):
        snapshot = client.request(
            "POST",
            f"/v1/agent/runs/{run_id}/direction",
            {"actor": actor, "message": message},
        )
    ui.notice(
        "DIRECTION ACCEPTED",
        "The direction is durable and has priority over the previous strategy on the next model decision.",
        tone="green",
    )
    return snapshot


def run_task(
    task: str,
    *,
    auto_confirm: bool = False,
    auto_mode: bool | None = None,
    client: legacy.Client | None = None,
    config: dict[str, Any] | None = None,
    ui: ObservatoryTerminal | None = None,
    new_conversation: bool = False,
) -> int:
    task = str(task or "").strip()
    if not task:
        raise legacy.CLIError("task cannot be empty")
    if config is None:
        _path, config = base._config()
    ui = ui or ObservatoryTerminal(observe_mode=observe_mode(config))
    ui.set_observe_mode(observe_mode(config), announce=False)
    client = client or base._client(config)
    with ui.busy("WORKSPACE / RESOLVE KERNELS + COGNITIVE FIELD"):
        base.ensure_workspace(client, config, Path.cwd().resolve())
    durable._scan_memory(client, config, ui)
    actor = str(config.get("actor") or getpass.getuser())
    ui.task_banner(task)
    with ui.busy("BRAIN / START DURABLE RUN"):
        snapshot = client.request(
            "POST",
            "/v1/agent/runs",
            {
                "task": task,
                "workspace_id": config["workspace"],
                "actor": actor,
                "mode": config.get("mode") or "auto",
                "max_steps": 48,
                "auto_confirm": bool(auto_confirm),
                "conversation_id": None if new_conversation else config.get("conversation_id"),
                "new_conversation": bool(new_conversation),
            },
        )
    run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
    if run.get("id"):
        config["last_run_id"] = run["id"]
    conversation = snapshot.get("conversation") if isinstance(snapshot.get("conversation"), dict) else {}
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
    base._save(config)
    snapshot = durable._drive_run(
        client,
        snapshot,
        actor=actor,
        ui=ui,
        auto_mode_available=(auto_mode_enabled(config) if auto_mode is None else bool(auto_mode)),
    )
    conversation = snapshot.get("conversation") if isinstance(snapshot.get("conversation"), dict) else {}
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
        base._save(config)
    return 0


def _show_agents(client, config: dict[str, Any], ui: ObservatoryTerminal) -> None:
    run_id = str(config.get("last_run_id") or "")
    if not run_id:
        ui.notice("AGENTS", "No Run has been started in this terminal yet.", tone="cyan")
        return
    with ui.busy("AGENT FIELD / LOAD"):
        payload = client.request("GET", f"/v1/agent/runs/{run_id}/agents")
    ui.agents(payload)


def _show_tokens(client, config: dict[str, Any], ui: ObservatoryTerminal) -> None:
    run_id = str(config.get("last_run_id") or "")
    if not run_id:
        ui.notice("TOKENS", "No Run has been started in this terminal yet.", tone="cyan")
        return
    with ui.busy("TOKEN RECEIPTS / LOAD"):
        payload = client.request("GET", f"/v1/agent/runs/{run_id}/usage")
    ui.tokens(payload)


def _show_cognition(client, config: dict[str, Any], ui: ObservatoryTerminal) -> None:
    run_id = str(config.get("last_run_id") or "")
    if not run_id:
        ui.notice("COGNITION", "No Run has been started in this terminal yet.", tone="cyan")
        return
    with ui.busy("COGNITIVE CONTINUITY / LOAD"):
        payload = client.request("GET", f"/v1/agent/runs/{run_id}/cognition")
    ui.cognition(payload)


def interactive() -> int:
    _path, config = base._config()
    if AUTO_MODE_KEY not in config:
        config[AUTO_MODE_KEY] = True
    if OBSERVE_MODE_KEY not in config:
        config[OBSERVE_MODE_KEY] = "balanced"
    base._save(config)
    ui = ObservatoryTerminal(observe_mode=observe_mode(config))
    client = base._client(config)
    current_project = Path.cwd().resolve()
    with ui.busy("WORKSPACE / BIND SYSTEM + DESKTOP"):
        base.ensure_workspace(client, config, current_project)
    durable._scan_memory(client, config, ui)
    base._redraw(ui, config)
    _auto_command("status", config=config, ui=ui)
    _observe_command("status", config=config, ui=ui)
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
                "LIGHTHOUSE 1.4",
                "/observe off|focus|balanced|verbose — control cognitive and engineering detail\n"
                "/cognition — show the latest durable Cognitive State\n"
                "/steer <direction> — redirect the latest active Run\n"
                "/auto on|off — offer or hide Run-wide Auto at action time\n"
                "/agents — show Agent count, roles, progress and Bus advice\n"
                "/tokens — show Run and conversation Token receipts",
                tone="cyan",
            )
        elif line == "/new":
            config.pop("conversation_id", None)
            config.pop("last_run_id", None)
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
            _observe_command("status", config=config, ui=ui)
            base.doctor(ui=ui)
        elif line == "/agents":
            _show_agents(client, config, ui)
        elif line == "/tokens":
            _show_tokens(client, config, ui)
        elif line == "/cognition":
            _show_cognition(client, config, ui)
        elif line.startswith("/steer"):
            _steer_run(
                client,
                config,
                ui,
                run_id=str(config.get("last_run_id") or ""),
                message=line[len("/steer"):].strip(),
            )
        elif line.startswith("/observe"):
            _observe_command(line[len("/observe"):].strip(), config=config, ui=ui)
        elif line.startswith("/auto"):
            _auto_command(line[len("/auto"):].strip(), config=config, ui=ui)
        elif line.startswith("/capabilities"):
            base._capability_view(client, config, ui, line[len("/capabilities"):].strip())
        elif line.startswith("/mode"):
            requested = line[len("/mode"):].strip().lower()
            if requested not in {"auto", "system", "data", "desktop"}:
                ui.notice("MODE", "Use /mode auto, /mode system, /mode data or /mode desktop.", tone="amber")
            else:
                config["mode"] = requested
                base._save(config)
                ui.notice("KERNEL PROFILE", f"Active mode changed to {requested.upper()}.", tone="cyan")
        elif line.startswith("/init"):
            base.init_project(line[len("/init"):].strip() or os.getcwd(), ui=ui)
            _path, config = base._config()
            client = base._client(config)
            ui.set_observe_mode(observe_mode(config), announce=False)
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
                receipt = client.request("GET", f"/v1/operations/{operation_id}/receipt")
            ui.receipt(receipt)
        elif line.startswith("!"):
            command = line[1:].strip()
            if not command:
                ui.notice("EXACT COMMAND", "Prefix a complete lh command with !", tone="amber")
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
    base._scan_memory = durable._scan_memory
    base._drive_run = durable._drive_run
    base.run_task = run_task
    base.interactive = interactive
    if argv and argv[0] == "auto":
        ui = ObservatoryTerminal()
        _path, config = base._config()
        _auto_command(" ".join(argv[1:]) if len(argv) > 1 else "status", config=config, ui=ui)
        return 0
    if argv and argv[0] == "observe":
        _path, config = base._config()
        ui = ObservatoryTerminal(observe_mode=observe_mode(config))
        _observe_command(" ".join(argv[1:]) if len(argv) > 1 else "status", config=config, ui=ui)
        return 0
    if argv and argv[0] == "steer":
        if len(argv) < 3:
            raise legacy.CLIError('usage: lh steer <run-id> "direction"')
        _path, config = base._config()
        ui = ObservatoryTerminal(observe_mode=observe_mode(config))
        client = base._client(config)
        _steer_run(client, config, ui, run_id=argv[1], message=" ".join(argv[2:]))
        return 0
    return durable_base_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
