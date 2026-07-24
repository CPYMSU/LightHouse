from __future__ import annotations

import argparse
import getpass
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time
from typing import Any
from urllib.parse import urlencode

from . import cli as legacy
from .secrets import (
    CONTROL_KEY_SERVICE,
    MODEL_KEY_SERVICE,
    control_api_key,
    keychain_delete,
    keychain_set,
)
from .ui import SwissTerminal


LEGACY_COMMANDS = {
    "status", "migrate", "capabilities", "targets", "target-add",
    "workspaces", "workspace-add", "configure", "use", "mode", "run",
    "confirm", "operation", "events", "receipt", "agent", "agent-show",
    "agent-resume", "agent-input", "agent-events",
}


def _config() -> tuple[Path, dict[str, Any]]:
    args = argparse.Namespace(config=None)
    path = legacy.config_path(args)
    return path, legacy.load_config(path)


def _save(config: dict[str, Any]) -> None:
    path, _current = _config()
    legacy.save_config(path, config)


def _base_url(config: dict[str, Any]) -> str:
    return os.environ.get("LIGHTHOUSE_URL") or str(config.get("url") or "") or "http://127.0.0.1:8787"


def _client(config: dict[str, Any]) -> legacy.Client:
    key = control_api_key()
    if len(key) < 16:
        raise legacy.CLIError("LightHouse control credential is missing. Run the macOS installer or `lh login`.")
    os.environ["LIGHTHOUSE_API_KEY"] = key
    return legacy.Client(_base_url(config), key)


def _restart_launch_agent() -> None:
    if sys.platform != "darwin":
        return
    subprocess.run(
        ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.cpym.su.lighthouse"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def login(*, ui: SwissTerminal | None = None) -> int:
    if sys.platform != "darwin":
        raise legacy.CLIError("`lh login` currently stores credentials in macOS Keychain. Use environment variables on other systems.")
    path, config = _config()
    if len(control_api_key()) < 16:
        control = getpass.getpass("LightHouse control key (leave blank to generate): ").strip() or os.urandom(32).hex()
        keychain_set(CONTROL_KEY_SERVICE, control)
    base_default = str(config.get("model_base_url") or "")
    model_default = str(config.get("model") or "")
    base = input(f"Model API base URL{f' [{base_default}]' if base_default else ''}: ").strip() or base_default
    model = input(f"Model name{f' [{model_default}]' if model_default else ''}: ").strip() or model_default
    model_key = getpass.getpass("Model API key: ").strip()
    if not base or not model or not model_key:
        raise legacy.CLIError("model base URL, model name and API key are required")
    keychain_set(MODEL_KEY_SERVICE, model_key)
    config.update({
        "url": _base_url(config),
        "model_base_url": base,
        "model": model,
        "model_json_mode": True,
        "actor": config.get("actor") or getpass.getuser(),
    })
    legacy.save_config(path, config)
    _restart_launch_agent()
    (ui or SwissTerminal()).notice("CREDENTIALS", "Model credentials saved in macOS Keychain.", tone="green")
    return 0


def logout(*, ui: SwissTerminal | None = None) -> int:
    removed = keychain_delete(MODEL_KEY_SERVICE)
    message = "Model credential removed." if removed else "No model credential was stored."
    (ui or SwissTerminal()).notice("CREDENTIALS", message, tone="cyan")
    return 0


def _project_test_command(path: Path) -> str | None:
    if (path / "pyproject.toml").exists() or (path / "pytest.ini").exists():
        return "python -m pytest -q"
    if (path / "package.json").exists():
        return "npm test"
    if (path / "Cargo.toml").exists():
        return "cargo test"
    return None


def _named(items: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    return next((item for item in items if item.get("name") == name), None)


def ensure_workspace(client: legacy.Client, config: dict[str, Any], project_path: Path) -> str:
    """Create/reuse the local System + Desktop execution coordinate."""
    project_path = project_path.expanduser().resolve()
    if not project_path.is_dir():
        raise legacy.CLIError(f"project path does not exist: {project_path}")
    previous_workspace = str(config.get("workspace") or "")
    identity = hashlib.sha256(str(project_path).encode("utf-8")).hexdigest()[:12]
    system_name = f"local-system-{identity}"
    desktop_name = f"local-desktop-{identity}"
    workspace_name = f"workspace-{identity}-desktop"

    targets = client.request("GET", "/v1/targets").get("items", [])
    system_target = _named(targets, system_name)
    if system_target is None:
        target_config: dict[str, Any] = {
            "transport": "local",
            "default_cwd": str(project_path),
            "allowed_roots": [str(project_path)],
            "shell": "/bin/bash",
        }
        test_command = _project_test_command(project_path)
        if test_command:
            target_config["test_command"] = test_command
        system_target = client.request("POST", "/v1/targets", {
            "name": system_name,
            "kind": "system",
            "config": target_config,
        })

    desktop_target = _named(targets, desktop_name)
    if desktop_target is None:
        desktop_target = client.request("POST", "/v1/targets", {
            "name": desktop_name,
            "kind": "desktop",
            "config": {
                "platform": "macos",
                "default_cwd": str(project_path),
                "allowed_roots": [str(project_path)],
                "allowed_apps": ["Safari", "Google Chrome", "Firefox", "Arc", "Finder"],
                "allowed_schemes": ["http", "https", "file"],
                "browser": "default",
            },
        })

    workspaces = client.request("GET", "/v1/workspaces").get("items", [])
    workspace = _named(workspaces, workspace_name)
    if workspace is None:
        workspace = client.request("POST", "/v1/workspaces", {
            "name": workspace_name,
            "data_target_id": None,
            "system_target_id": system_target["id"],
            "desktop_target_id": desktop_target["id"],
        })

    if previous_workspace and previous_workspace != str(workspace["id"]):
        config.pop("conversation_id", None)
        config.pop("memory_scanned_workspace", None)
    config.update({
        "workspace": workspace["id"],
        "workspace_name": workspace_name,
        "mode": "auto",
        "actor": config.get("actor") or getpass.getuser(),
        "project_path": str(project_path),
        "desktop_target": desktop_target["id"],
    })
    _save(config)
    return str(workspace["id"])


def _scan_memory(
    client: legacy.Client,
    config: dict[str, Any],
    ui: SwissTerminal,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    workspace = str(config.get("workspace") or "")
    if not workspace:
        return None
    if not force and config.get("memory_scanned_workspace") == workspace:
        return None
    try:
        with ui.busy("MEMORY / INDEX AUTHORIZED FILES"):
            result = client.request("POST", "/v1/memory/scan", {
                "workspace_id": workspace,
                "max_files": 5000,
            })
        config["memory_scanned_workspace"] = workspace
        _save(config)
        if force:
            ui.notice(
                "MEMORY INDEX",
                f"Indexed {result.get('indexed', 0)} files; skipped {result.get('skipped', 0)}.",
                tone="green",
            )
        return result
    except Exception as exc:
        ui.notice("MEMORY INDEX", f"Index refresh was skipped: {exc}", tone="amber")
        return None


def init_project(path: str | None = None, *, ui: SwissTerminal | None = None) -> int:
    ui = ui or SwissTerminal()
    _path, config = _config()
    client = _client(config)
    project = Path(path or os.getcwd())
    with ui.busy("WORKSPACE / BIND SYSTEM + DESKTOP"):
        workspace_id = ensure_workspace(client, config, project)
    _scan_memory(client, config, ui)
    ui.notice("WORKSPACE READY", f"{project.resolve()}\nWorkspace {workspace_id}\nKernels SYSTEM + DESKTOP", tone="green")
    return 0


def doctor(*, ui: SwissTerminal | None = None) -> int:
    ui = ui or SwissTerminal()
    _path, config = _config()
    checks: dict[str, Any] = {
        "control_key": len(control_api_key()) >= 16,
        "model_config": bool(config.get("model_base_url") and config.get("model")),
        "url": _base_url(config),
        "workspace": config.get("workspace"),
        "project_path": config.get("project_path"),
        "desktop_target": config.get("desktop_target"),
        "conversation": config.get("conversation_id"),
        "memory_index": config.get("memory_scanned_workspace") == config.get("workspace"),
        "kernel_profile": config.get("mode") or "auto",
    }
    try:
        checks["api"] = _client(config).request("GET", "/healthz")
    except Exception as exc:
        checks["api"] = {"ok": False, "error": str(exc)}
    ui.doctor(checks)
    return 0 if checks["control_key"] else 2


def _await_operation(
    client: legacy.Client,
    operation_id: str,
    *,
    timeout: float = 660.0,
    poll_interval: float = 0.25,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last = client.request("GET", f"/v1/operations/{operation_id}")
        except Exception:
            time.sleep(min(1.0, poll_interval * 2))
            continue
        status = str((last.get("operation") or {}).get("status") or "")
        if status in {"succeeded", "failed", "cancelled"}:
            return last
        time.sleep(poll_interval)
    return last or {"operation": {"id": operation_id, "status": "running"}, "receipt": None, "events": []}


def _drive_run(client: legacy.Client, snapshot: dict[str, Any], *, actor: str, ui: SwissTerminal) -> dict[str, Any]:
    seen: set[tuple[Any, ...]] = set()
    while True:
        seen = ui.render_run(snapshot, seen=seen)
        run = snapshot.get("run") or {}
        status = str(run.get("status") or "")
        if status == "awaiting_confirmation":
            pending = snapshot.get("pending_operation") or {}
            ui.confirmation(pending)
            if not ui.confirm():
                ui.notice("PAUSED", "The frozen operation remains pending. Resume it later with its run ID.", tone="amber")
                return snapshot
            operation = pending.get("operation") or {}
            operation_id = str(operation.get("id") or "")
            try:
                client.request(
                    "POST",
                    f"/v1/operations/{operation_id}/confirm-deferred",
                    {"actor": actor},
                )
            except Exception:
                # The POST response is not execution truth. Poll the immutable Operation.
                pass
            with ui.busy("EXECUTE / WAIT FOR OPERATION RECEIPT"):
                operation_snapshot = _await_operation(client, operation_id)
            operation_status = str((operation_snapshot.get("operation") or {}).get("status") or "")
            if operation_status == "running":
                ui.notice(
                    "OPERATION CONTINUES",
                    f"Operation {operation_id} is still running and remains recoverable. Resume run {run.get('id')} later.",
                    tone="amber",
                )
                return snapshot
            if operation_snapshot.get("receipt"):
                ui.receipt(operation_snapshot["receipt"])
            with ui.busy("BRAIN / CONTINUE FROM RECEIPT"):
                snapshot = client.request("POST", f"/v1/agent/runs/{run['id']}/advance", {})
            continue
        if status == "waiting_input":
            message = ui.input_required(str(run.get("final_message") or ""))
            if not message:
                ui.notice("PAUSED", "The run is waiting for additional input.", tone="amber")
                return snapshot
            with ui.busy("BRAIN / CONTINUE WITH INPUT"):
                snapshot = client.request(
                    "POST",
                    f"/v1/agent/runs/{run['id']}/input",
                    {"actor": actor, "message": message},
                )
            continue
        ui.final(snapshot)
        return snapshot


def run_task(
    task: str,
    *,
    auto_confirm: bool = False,
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
        _path, config = _config()
    client = client or _client(config)
    project = Path.cwd().resolve()
    with ui.busy("WORKSPACE / RESOLVE THREE KERNELS"):
        ensure_workspace(client, config, project)
    _scan_memory(client, config, ui)
    actor = str(config.get("actor") or getpass.getuser())
    ui.task_banner(task)
    with ui.busy("THINK / BUILD PLAN WITH MEMORY"):
        snapshot = client.request("POST", "/v1/agent/runs", {
            "task": task,
            "workspace_id": config["workspace"],
            "actor": actor,
            "mode": config.get("mode") or "auto",
            "max_steps": 24,
            "auto_confirm": bool(auto_confirm),
            "conversation_id": None if new_conversation else config.get("conversation_id"),
            "new_conversation": bool(new_conversation),
        })
    conversation = snapshot.get("conversation") if isinstance(snapshot.get("conversation"), dict) else {}
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
        _save(config)
    snapshot = _drive_run(client, snapshot, actor=actor, ui=ui)
    conversation = snapshot.get("conversation") if isinstance(snapshot.get("conversation"), dict) else {}
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
        _save(config)
    return 0


def _redraw(ui: SwissTerminal, config: dict[str, Any], *, brain: str = "READY") -> None:
    ui.clear()
    ui.masthead(
        mode=str(config.get("mode") or "auto"),
        workspace=str(config.get("workspace_name") or config.get("workspace") or "local"),
        project=str(config.get("project_path") or os.getcwd()),
        brain=brain,
    )
    ui.guide()
    ui.terminal_size_warning()


def _capability_view(client: legacy.Client, config: dict[str, Any], ui: SwissTerminal, query: str = "") -> None:
    params = urlencode({"q": query, "kernel": config.get("mode") or "auto", "limit": 100})
    with ui.busy("ATLAS / LOAD AUTHORIZED CAPABILITIES"):
        value = client.request("GET", "/v1/capabilities?" + params)
    ui.capabilities(value.get("items") or [])


def _legacy_passthrough(line: str, ui: SwissTerminal) -> None:
    command = line[1:].strip()
    if not command:
        ui.notice("EXACT COMMAND", "Prefix a complete lh command with !", tone="amber")
        return
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        ui.error(str(exc))
        return
    ui.section("EXACT COMMAND", command)
    legacy.main(argv)


def interactive() -> int:
    ui = SwissTerminal()
    _path, config = _config()
    client = _client(config)
    current_project = Path.cwd().resolve()
    with ui.busy("WORKSPACE / BIND SYSTEM + DESKTOP"):
        ensure_workspace(client, config, current_project)
    _scan_memory(client, config, ui)
    _redraw(ui, config)
    history_path = Path.home() / ".lighthouse" / "history"
    session = ui.session(history_path)
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
            _redraw(ui, config)
            continue
        if line == "/help":
            ui.help()
            continue
        if line == "/new":
            config.pop("conversation_id", None)
            _save(config)
            ui.notice("NEW CONVERSATION", "The next task starts a new conversation while retaining indexed long-term memory.", tone="cyan")
            continue
        if line == "/reindex":
            _scan_memory(client, config, ui, force=True)
            continue
        if line == "/status":
            _redraw(ui, config)
            doctor(ui=ui)
            continue
        if line.startswith("/capabilities"):
            _capability_view(client, config, ui, line[len("/capabilities"):].strip())
            continue
        if line.startswith("/mode"):
            requested = line[len("/mode"):].strip().lower()
            if requested not in {"auto", "system", "data", "desktop"}:
                ui.notice("MODE", "Use /mode auto, /mode system, /mode data or /mode desktop.", tone="amber")
                continue
            config["mode"] = requested
            _save(config)
            ui.notice("KERNEL PROFILE", f"Active mode changed to {requested.upper()}.", tone="cyan")
            continue
        if line.startswith("/init"):
            init_project(line[len("/init"):].strip() or os.getcwd(), ui=ui)
            _path, config = _config()
            client = _client(config)
            _redraw(ui, config)
            continue
        if line == "/doctor":
            doctor(ui=ui)
            continue
        if line == "/login":
            login(ui=ui)
            _path, config = _config()
            client = _client(config)
            continue
        if line.startswith("/receipt "):
            operation_id = line.split(maxsplit=1)[1]
            with ui.busy("RECEIPT / LOAD"):
                receipt = client.request("GET", f"/v1/operations/{operation_id}/receipt")
            ui.receipt(receipt)
            continue
        if line.startswith("!"):
            _legacy_passthrough(line, ui)
            continue
        run_task(line, client=client, config=config, ui=ui)


def help_text() -> None:
    print("""LightHouse OS — integrated LightHouse terminal

  lh                         open the Swiss interactive terminal
  lh "task"                  run one natural-language task across kernels
  lh init [PATH]             bind System + Desktop targets for a project
  lh login                   store the model key in macOS Keychain
  lh doctor                  verify installation, conversation and memory index
  lh capabilities            list governed capabilities
  lh mode desktop            select only the Desktop Kernel
  /new                       start a new conversation, keep long-term memory
  /reindex                   refresh the authorized file index
  lh run ...                 execute an exact capability
  lh agent ...               compatibility alias for scripted runs
""")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    ui = SwissTerminal()
    try:
        if not argv:
            return interactive()
        first = argv[0]
        if first in {"help", "--help", "-h"}:
            help_text()
            return 0
        if first == "login":
            return login(ui=ui)
        if first == "logout":
            return logout(ui=ui)
        if first == "init":
            return init_project(argv[1] if len(argv) > 1 else None, ui=ui)
        if first == "doctor":
            return doctor(ui=ui)
        if first not in LEGACY_COMMANDS and not first.startswith("-"):
            return run_task(" ".join(argv), ui=ui)
        key = control_api_key()
        if key:
            os.environ["LIGHTHOUSE_API_KEY"] = key
        return legacy.main(argv)
    except (legacy.CLIError, OSError, ValueError) as exc:
        ui.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
