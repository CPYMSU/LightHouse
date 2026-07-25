from __future__ import annotations

import getpass
from pathlib import Path
import time
from typing import Any

from . import cli as legacy
from . import terminal as base
from .ui import SwissTerminal


def _scan_memory(
    client: legacy.Client,
    config: dict[str, Any],
    ui: SwissTerminal,
    *,
    force: bool = False,
) -> dict[str, Any] | None:
    workspace = str(config.get("workspace") or "")
    if not workspace or (not force and config.get("memory_scanned_workspace") == workspace):
        return None
    try:
        with ui.busy("MEMORY / SCHEDULE BACKGROUND DISTILLATION"):
            result = client.request(
                "POST",
                "/v1/memory/scan",
                {"workspace_id": workspace, "max_files": 5000},
            )
        config["memory_scanned_workspace"] = workspace
        base._save(config)
        if force:
            if result.get("queued"):
                job = result.get("job") if isinstance(result.get("job"), dict) else {}
                ui.notice(
                    "MEMORY STEWARD",
                    "The authorized Workspace scan was queued in the background."
                    + (f"\nJob {job.get('id')}" if job.get("id") else ""),
                    tone="green",
                )
            else:
                ui.notice(
                    "MEMORY INDEX",
                    f"Indexed {result.get('indexed', 0)} files and "
                    f"{result.get('directories_indexed', 0)} directories; "
                    f"skipped {result.get('skipped', 0)}.",
                    tone="green",
                )
        return result
    except Exception as exc:
        ui.notice(
            "MEMORY STEWARD",
            f"Background index refresh was skipped: {exc}",
            tone="amber",
        )
        return None


def _await_run(
    client: legacy.Client,
    run_id: str,
    *,
    timeout: float = 660.0,
    poll_interval: float = 0.25,
) -> dict[str, Any]:
    """Compatibility helper for callers that do not need streamed observability."""
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            last = client.request("GET", f"/v1/agent/runs/{run_id}")
        except Exception:
            time.sleep(min(1.0, poll_interval * 2))
            continue
        status = str((last.get("run") or {}).get("status") or "")
        if status not in {"created", "running"}:
            return last
        time.sleep(poll_interval)
    return last or {
        "run": {"id": run_id, "status": "running"},
        "steps": [],
        "pending_operation": None,
    }


def _drive_run(
    client: legacy.Client,
    snapshot: dict[str, Any],
    *,
    actor: str,
    ui: SwissTerminal,
    auto_mode_available: bool = False,
) -> dict[str, Any]:
    seen: set[tuple[Any, ...]] = set()
    run_deadline = time.monotonic() + 660.0
    poll_interval = 0.35
    consecutive_poll_errors = 0
    while True:
        seen = ui.render_run(snapshot, seen=seen)
        run = snapshot.get("run") or {}
        run_id = str(run.get("id") or "")
        status = str(run.get("status") or "")
        if status in {"created", "running"}:
            if time.monotonic() >= run_deadline:
                ui.notice(
                    "BRAIN CONTINUES",
                    f"Run {run_id} is still active and remains recoverable. "
                    "Its state, Agent work and Token receipts are durable even if this terminal closes.",
                    tone="amber",
                )
                return snapshot
            try:
                snapshot = client.request("GET", f"/v1/agent/runs/{run_id}")
                consecutive_poll_errors = 0
            except Exception as exc:
                consecutive_poll_errors += 1
                if consecutive_poll_errors == 4:
                    ui.notice(
                        "CONTROL PLANE RETRY",
                        f"The Run remains durable while the terminal reconnects: {exc}",
                        tone="amber",
                    )
                time.sleep(min(2.0, poll_interval * (consecutive_poll_errors + 1)))
                continue
            time.sleep(poll_interval)
            continue
        if status == "awaiting_confirmation":
            pending = snapshot.get("pending_operation") or {}
            ui.confirmation(pending)
            chooser = getattr(ui, "permission_choice", None)
            if callable(chooser):
                choice = chooser(auto_available=auto_mode_available)
            else:
                choice = "once" if ui.confirm() else "deny"
            if choice == "deny":
                ui.notice(
                    "PAUSED",
                    "The frozen operation remains pending. Resume it later with its Run ID.",
                    tone="amber",
                )
                return snapshot
            operation_id = str(((pending.get("operation") or {}).get("id")) or "")
            if choice == "auto":
                try:
                    snapshot = client.request(
                        "POST",
                        f"/v1/agent/runs/{run_id}/auto-authorize",
                        {"actor": actor},
                    )
                    ui.notice(
                        "AUTO MODE / RUN SCOPED",
                        "This Run may auto-confirm compatible operations inside the displayed target and roots. Scope expansion asks again.",
                        tone="green",
                    )
                except Exception as exc:
                    ui.notice(
                        "AUTO MODE",
                        f"Run scope could not be granted; this operation will be allowed once: {exc}",
                        tone="amber",
                    )
            try:
                client.request(
                    "POST",
                    f"/v1/operations/{operation_id}/confirm-deferred",
                    {"actor": actor},
                )
            except Exception:
                pass
            with ui.busy("EXECUTE / WAIT FOR OPERATION RECEIPT"):
                operation_snapshot = base._await_operation(client, operation_id)
            operation_status = str((operation_snapshot.get("operation") or {}).get("status") or "")
            if operation_status == "running":
                ui.notice(
                    "OPERATION CONTINUES",
                    f"Operation {operation_id} is still running and remains recoverable. "
                    f"Resume Run {run_id} later.",
                    tone="amber",
                )
                return snapshot
            if operation_snapshot.get("receipt"):
                ui.receipt(operation_snapshot["receipt"])
            snapshot = client.request(
                "POST",
                f"/v1/agent/runs/{run_id}/advance",
                {},
            )
            continue
        if status == "waiting_input":
            message = ui.input_required(str(run.get("final_message") or ""))
            if not message:
                ui.notice("PAUSED", "The Run is waiting for additional input.", tone="amber")
                return snapshot
            snapshot = client.request(
                "POST",
                f"/v1/agent/runs/{run_id}/input",
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
        _path, config = base._config()
    client = client or base._client(config)
    with ui.busy("WORKSPACE / RESOLVE THREE KERNELS"):
        base.ensure_workspace(client, config, Path.cwd().resolve())
    _scan_memory(client, config, ui)
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
                "max_steps": 24,
                "auto_confirm": bool(auto_confirm),
                "conversation_id": None if new_conversation else config.get("conversation_id"),
                "new_conversation": bool(new_conversation),
            },
        )
    conversation = snapshot.get("conversation") if isinstance(snapshot.get("conversation"), dict) else {}
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
        base._save(config)
    snapshot = _drive_run(
        client,
        snapshot,
        actor=actor,
        ui=ui,
        auto_mode_available=bool(config.get("auto_mode", True)),
    )
    conversation = snapshot.get("conversation") if isinstance(snapshot.get("conversation"), dict) else {}
    if conversation.get("id"):
        config["conversation_id"] = conversation["id"]
        base._save(config)
    return 0


def main(argv: list[str] | None = None) -> int:
    base._scan_memory = _scan_memory
    base._drive_run = _drive_run
    base.run_task = run_task
    return base.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
