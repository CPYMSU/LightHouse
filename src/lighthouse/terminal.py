from __future__ import annotations

import argparse
import getpass
import hashlib
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from . import cli as legacy
from .secrets import (
    CONTROL_KEY_SERVICE,
    MODEL_KEY_SERVICE,
    control_api_key,
    keychain_delete,
    keychain_set,
)


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


def login() -> int:
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
    print("LightHouse credentials saved in macOS Keychain.")
    return 0


def logout() -> int:
    removed = keychain_delete(MODEL_KEY_SERVICE)
    print("Model credential removed." if removed else "No model credential was stored.")
    return 0


def _project_test_command(path: Path) -> str | None:
    if (path / "pyproject.toml").exists() or (path / "pytest.ini").exists():
        return "python -m pytest -q"
    if (path / "package.json").exists():
        return "npm test"
    if (path / "Cargo.toml").exists():
        return "cargo test"
    return None


def ensure_workspace(client: legacy.Client, config: dict[str, Any], project_path: Path) -> str:
    project_path = project_path.expanduser().resolve()
    if not project_path.is_dir():
        raise legacy.CLIError(f"project path does not exist: {project_path}")
    identity = hashlib.sha256(str(project_path).encode("utf-8")).hexdigest()[:12]
    target_name = f"local-{identity}"
    workspace_name = f"workspace-{identity}"
    targets = client.request("GET", "/v1/targets").get("items", [])
    target = next((item for item in targets if item.get("name") == target_name), None)
    if target is None:
        target_config: dict[str, Any] = {
            "transport": "local",
            "default_cwd": str(project_path),
            "allowed_roots": [str(project_path)],
            "shell": "/bin/bash",
        }
        test_command = _project_test_command(project_path)
        if test_command:
            target_config["test_command"] = test_command
        target = client.request("POST", "/v1/targets", {"name": target_name, "kind": "system", "config": target_config})
    workspaces = client.request("GET", "/v1/workspaces").get("items", [])
    workspace = next((item for item in workspaces if item.get("name") == workspace_name), None)
    if workspace is None:
        workspace = client.request("POST", "/v1/workspaces", {
            "name": workspace_name,
            "data_target_id": None,
            "system_target_id": target["id"],
        })
    config.update({
        "workspace": workspace["id"],
        "mode": "system",
        "actor": config.get("actor") or getpass.getuser(),
        "project_path": str(project_path),
    })
    _save(config)
    return str(workspace["id"])


def init_project(path: str | None = None) -> int:
    _path, config = _config()
    client = _client(config)
    project = Path(path or os.getcwd())
    workspace_id = ensure_workspace(client, config, project)
    print(f"LightHouse initialized for {project.resolve()}")
    print(f"Workspace: {workspace_id}")
    return 0


def doctor() -> int:
    _path, config = _config()
    checks: dict[str, Any] = {
        "control_key": len(control_api_key()) >= 16,
        "model_config": bool(config.get("model_base_url") and config.get("model")),
        "url": _base_url(config),
        "workspace": config.get("workspace"),
        "project_path": config.get("project_path"),
    }
    try:
        checks["api"] = _client(config).request("GET", "/healthz")
    except Exception as exc:
        checks["api"] = {"ok": False, "error": str(exc)}
    legacy.print_json(checks)
    return 0 if checks["control_key"] else 2


def _print_run(snapshot: dict[str, Any]) -> None:
    run = snapshot.get("run") or {}
    print(f"\n[{run.get('status') or 'unknown'}] run {run.get('id', '')}")
    if run.get("final_message"):
        print(run["final_message"])


def _drive_run(client: legacy.Client, snapshot: dict[str, Any], *, actor: str) -> dict[str, Any]:
    while True:
        run = snapshot.get("run") or {}
        status = str(run.get("status") or "")
        if status == "awaiting_confirmation":
            pending = snapshot.get("pending_operation") or {}
            operation = pending.get("operation") or {}
            print("\nLightHouse requests confirmation:")
            print(f"  capability: {operation.get('capability')}")
            print(f"  operation:  {operation.get('id')}")
            answer = input("Confirm this frozen operation? [y/N] ").strip().lower()
            if answer not in {"y", "yes"}:
                return snapshot
            client.request("POST", f"/v1/operations/{operation['id']}/confirm", {"actor": actor})
            snapshot = client.request("POST", f"/v1/agent/runs/{run['id']}/advance", {})
            continue
        if status == "waiting_input":
            message = input("LightHouse needs more information: ").strip()
            if not message:
                return snapshot
            snapshot = client.request("POST", f"/v1/agent/runs/{run['id']}/input", {"actor": actor, "message": message})
            continue
        _print_run(snapshot)
        return snapshot


def run_task(task: str, *, auto_confirm: bool = False) -> int:
    task = str(task or "").strip()
    if not task:
        raise legacy.CLIError("task cannot be empty")
    _path, config = _config()
    client = _client(config)
    workspace = config.get("workspace")
    if not workspace:
        workspace = ensure_workspace(client, config, Path(config.get("project_path") or os.getcwd()))
    actor = str(config.get("actor") or getpass.getuser())
    snapshot = client.request("POST", "/v1/agent/runs", {
        "task": task,
        "workspace_id": workspace,
        "actor": actor,
        "mode": config.get("mode") or "system",
        "max_steps": 16,
        "auto_confirm": bool(auto_confirm),
    })
    _drive_run(client, snapshot, actor=actor)
    return 0


def interactive() -> int:
    _path, config = _config()
    client = _client(config)
    if not config.get("workspace"):
        ensure_workspace(client, config, Path.cwd())
    print("LightHouse OS")
    print(f"Project: {config.get('project_path')}")
    print("Type a task. Commands: /init PATH, /doctor, /login, /exit")
    while True:
        try:
            line = input("\nlh> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in {"/exit", "/quit"}:
            return 0
        if line.startswith("/init"):
            init_project(line[5:].strip() or os.getcwd())
            _path, config = _config()
            client = _client(config)
            continue
        if line == "/doctor":
            doctor()
            continue
        if line == "/login":
            login()
            _path, config = _config()
            client = _client(config)
            continue
        run_task(line)


def help_text() -> None:
    print("""LightHouse OS

  lh                         open the integrated LightHouse terminal
  lh \"task\"                  run one natural-language task
  lh init [PATH]             bind the current project to LightHouse
  lh login                   store the model key in macOS Keychain
  lh doctor                  verify the local installation
  lh capabilities            list governed capabilities
  lh run ...                 execute an exact capability
  lh agent ...               compatibility alias for scripted runs
""")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not argv:
            return interactive()
        first = argv[0]
        if first in {"help", "--help", "-h"}:
            help_text()
            return 0
        if first == "login":
            return login()
        if first == "logout":
            return logout()
        if first == "init":
            return init_project(argv[1] if len(argv) > 1 else None)
        if first == "doctor":
            return doctor()
        if first not in LEGACY_COMMANDS and not first.startswith("-"):
            return run_task(" ".join(argv))
        key = control_api_key()
        if key:
            os.environ["LIGHTHOUSE_API_KEY"] = key
        return legacy.main(argv)
    except (legacy.CLIError, OSError, ValueError) as exc:
        print(f"lh: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
