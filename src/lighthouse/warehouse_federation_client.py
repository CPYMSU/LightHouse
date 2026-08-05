from __future__ import annotations

import json
import threading
import time
from typing import Any, Mapping
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from . import __version__
from .deferred_runs import DeferredRunScheduler
from .models import AgentRunStatus, KernelMode
from .warehouse_federation import (
    WarehouseFederationConfig,
    load_warehouse_federation_config,
    warehouse_device_token,
)
from .warehouse_federation_protocol import (
    PROTOCOL,
    WarehouseFederationProtocolError,
    make_envelope,
    parse_warehouse_message,
    payload_digest,
    project_agent_step,
    project_run_result,
)
from .warehouse_federation_store import WarehouseFederationStore

_TERMINAL_RUN_STATUSES = {
    "succeeded",
    "completed_with_warning",
    "partially_completed",
    "failed",
    "cancelled",
}


class WarehouseFederationWorker:
    """Maintain the device-initiated WSS session and bridge remote Runs locally."""

    def __init__(
        self,
        *,
        runtime: Any,
        repository: Any,
        store: WarehouseFederationStore,
        instance_id: str,
        instance_name: str,
        poll_interval: float = 1.0,
    ) -> None:
        self.runtime = runtime
        self.repository = repository
        self.store = store
        self.instance_id = instance_id
        self.instance_name = instance_name
        self.poll_interval = max(0.25, float(poll_interval))
        self.scheduler = DeferredRunScheduler(runtime)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._connected = threading.Event()
        self._last_error: str | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="lighthouse-warehouse-federation",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(0.0, timeout))

    def status(self) -> dict[str, object]:
        return {
            "connected": self._connected.is_set(),
            "last_error": self._last_error,
            **self.store.status(),
        }

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            config = load_warehouse_federation_config()
            if config is None or not config.enabled:
                self._connected.clear()
                self._stop.wait(2.0)
                continue
            token = warehouse_device_token(config)
            if not token:
                self._connected.clear()
                self._last_error = "Warehouse device credential is missing"
                self._stop.wait(5.0)
                continue
            try:
                self._connect_once(config, token)
                delay = 1.0
            except Exception as exc:
                self._connected.clear()
                self._last_error = f"{type(exc).__name__}: {exc}"
                self._stop.wait(delay)
                delay = min(delay * 2.0, 30.0)

    def _connect_once(self, config: WarehouseFederationConfig, token: str) -> None:
        try:
            from websockets.exceptions import ConnectionClosed
            from websockets.sync.client import connect
        except ImportError as exc:
            raise RuntimeError("websockets dependency is not installed") from exc

        with connect(
            config.websocket_url,
            additional_headers={"Authorization": f"Bearer {token}"},
            open_timeout=20,
            close_timeout=5,
            max_size=1_000_000,
            ping_interval=20,
            ping_timeout=20,
        ) as websocket:
            self._connected.set()
            self._last_error = None
            hello = make_envelope("instance.hello", self._hello_payload(config))
            self._queue_and_send(websocket, hello)
            self._replay_outbox(websocket, skip_message_id=str(hello["message_id"]))
            last_heartbeat = time.monotonic()
            while not self._stop.is_set():
                current_config = load_warehouse_federation_config()
                if (
                    current_config is None
                    or not current_config.enabled
                    or current_config.origin != config.origin
                    or current_config.device_id != config.device_id
                ):
                    return
                self._sync_runs(websocket)
                now = time.monotonic()
                if now - last_heartbeat >= 20:
                    self._send(
                        websocket,
                        make_envelope(
                            "instance.heartbeat",
                            {
                                "instance_id": self.instance_id,
                                "load": self._load_projection(),
                            },
                        ),
                    )
                    last_heartbeat = now
                try:
                    raw = websocket.recv(timeout=self.poll_interval)
                except TimeoutError:
                    continue
                except ConnectionClosed:
                    return
                if raw is None:
                    return
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    value = json.loads(raw)
                except (TypeError, UnicodeError, json.JSONDecodeError) as exc:
                    raise WarehouseFederationProtocolError(
                        "Warehouse sent malformed JSON"
                    ) from exc
                self._handle_message(websocket, config, value)

    def _hello_payload(self, config: WarehouseFederationConfig) -> dict[str, object]:
        workspaces = [
            {"id": item.id, "name": item.name}
            for item in self.repository.list_workspaces()
        ]
        capabilities = [
            {
                "tool_name": item.tool_name,
                "kernel": item.kernel.value,
                "writes": item.writes,
                "risk": item.risk.value,
                "confirmation": item.confirmation.value,
            }
            for item in self.runtime.kernel.registry.list()
        ]
        return {
            "instance_id": self.instance_id,
            "instance_name": self.instance_name,
            "version": __version__,
            "protocols": [PROTOCOL],
            "device_id": config.device_id,
            "workspaces": workspaces,
            "capabilities": capabilities,
            "policy": {"remote_run_mode": "read_only"},
        }

    def _load_projection(self) -> dict[str, object]:
        active = self.store.list_active_runs()
        return {
            "active_remote_runs": len(active),
            "pending_outbox": self.store.status()["pending_outbox"],
        }

    def _handle_message(
        self,
        websocket: Any,
        config: WarehouseFederationConfig,
        raw: object,
    ) -> None:
        message = parse_warehouse_message(raw)
        message_type = str(message["type"])
        payload = message["payload"]
        assert isinstance(payload, Mapping)
        if message_type == "message.ack":
            self.store.mark_outbound_acknowledged(str(payload["message_id"]))
            return

        claimed, existing_status = self.store.claim_incoming(message)
        if not claimed:
            self._send_ack(
                websocket,
                str(message["message_id"]),
                accepted=existing_status == "handled",
            )
            return
        accepted = False
        error: str | None = None
        try:
            if message_type == "run.offer":
                self._handle_offer(websocket, config, payload)
            elif message_type == "run.input":
                self._handle_input(payload)
            elif message_type == "run.cancel":
                self._handle_cancel(websocket, payload)
            elif message_type in {
                "operation.approval_granted",
                "operation.approval_denied",
            }:
                raise WarehouseFederationProtocolError(
                    "Remote operation approvals are disabled in federation v1"
                )
            accepted = True
        except Exception as exc:
            error = str(exc)
            if message_type == "run.offer":
                try:
                    self.store.update_run(
                        str(payload.get("run_id") or ""),
                        status="rejected",
                        last_error=error,
                    )
                except (KeyError, ValueError):
                    pass
                self._queue_and_send(
                    websocket,
                    make_envelope(
                        "run.rejected",
                        {
                            "run_id": payload.get("run_id"),
                            "reason": error,
                        },
                    ),
                    remote_run_id=str(payload.get("run_id") or "") or None,
                )
        finally:
            self.store.finish_incoming(
                str(message["message_id"]),
                accepted=accepted,
                error=error,
            )
            self._send_ack(
                websocket,
                str(message["message_id"]),
                accepted=accepted,
                error=error,
            )

    def _handle_offer(
        self,
        websocket: Any,
        config: WarehouseFederationConfig,
        payload: Mapping[str, object],
    ) -> None:
        remote_run_id = str(payload["run_id"])
        policy = dict(payload["policy"])
        workspace = self._resolve_workspace(payload.get("workspace_ref"), config)
        actor = f"warehouse:{config.device_id}"
        local_run_id = str(uuid4())
        run, created = self.store.create_remote_run(
            remote_run_id=remote_run_id,
            local_run_id=local_run_id,
            warehouse_origin=config.origin,
            workspace_id=workspace.id,
            actor=actor,
            conversation_ref=(
                str(payload.get("conversation_ref"))
                if payload.get("conversation_ref")
                else None
            ),
            policy=policy,
        )
        local_run_id = str(run["local_run_id"])
        if not created and str(run["status"]) in {"rejected", "failed", "cancelled"}:
            raise ValueError(str(run.get("last_error") or "Remote Run is not reusable"))
        if created:
            self.scheduler.start(
                task=str(payload["goal"]),
                workspace_id=workspace.id,
                actor=actor,
                mode=KernelMode.AUTO,
                auto_confirm=False,
                conversation_id=None,
                new_conversation=True,
                work_intensity="balanced",
                run_id=local_run_id,
                launch=False,
            )
            self.store.update_run(remote_run_id, status="accepted")
        self._queue_and_send(
            websocket,
            make_envelope(
                "run.accepted",
                {
                    "run_id": remote_run_id,
                    "local_run_ref": local_run_id,
                    "workspace_ref": workspace.id,
                    "policy": policy,
                },
                message_id=uuid5(
                    NAMESPACE_URL,
                    f"{PROTOCOL}:accepted:{remote_run_id}:{local_run_id}",
                ),
            ),
            remote_run_id=remote_run_id,
        )
        if created:
            self.scheduler.launch(local_run_id)

    def _resolve_workspace(
        self,
        offered_ref: object,
        config: WarehouseFederationConfig,
    ) -> Any:
        requested = str(offered_ref or config.workspace_id or "").strip()
        if requested:
            try:
                return self.repository.get_workspace(requested)
            except KeyError:
                matches = [
                    item for item in self.repository.list_workspaces() if item.name == requested
                ]
                if len(matches) == 1:
                    return matches[0]
                raise ValueError("Warehouse workspace_ref is not mapped locally")
        workspaces = self.repository.list_workspaces()
        if len(workspaces) != 1:
            raise ValueError(
                "A default local workspace must be selected before accepting this Run"
            )
        return workspaces[0]

    def _handle_input(self, payload: Mapping[str, object]) -> None:
        remote = self.store.get_remote_run(str(payload["run_id"]))
        local_run_id = str(remote["local_run_id"])
        snapshot = self.runtime.snapshot(local_run_id)
        run = snapshot.get("run") or {}
        status = str(run.get("status") or "")
        if status == AgentRunStatus.WAITING_INPUT.value:
            self.scheduler.provide_input(
                local_run_id,
                actor=str(remote["actor"]),
                message=str(payload["text"]),
            )
            return
        direction = getattr(self.runtime, "provide_direction", None)
        if callable(direction):
            direction(
                local_run_id,
                actor=str(remote["actor"]),
                message=str(payload["text"]),
            )
            return
        raise ValueError("Local Run is not accepting input")

    def _handle_cancel(self, websocket: Any, payload: Mapping[str, object]) -> None:
        remote_run_id = str(payload["run_id"])
        remote = self.store.get_remote_run(remote_run_id)
        local_run_id = str(remote["local_run_id"])
        self.runtime.repository.append_agent_step(
            local_run_id,
            "run_cancelled",
            {
                "source": "warehouse_federation",
                "reason": str(payload["reason"]),
            },
        )
        self.runtime.repository.update_agent_run(
            local_run_id,
            status=AgentRunStatus.CANCELLED,
            final_message="Cancelled by Warehouse",
            goal_status="cancelled",
            response_status="cancelled",
            auto_confirm=False,
            auto_scope={},
        )
        result = {
            "status": "cancelled",
            "message": "Cancelled by Warehouse",
            "execution_status": "cancelled",
            "response_status": "cancelled",
            "goal_status": "cancelled",
            "warning": None,
            "updated_at": "",
        }
        digest = payload_digest(result)
        self._queue_and_send(
            websocket,
            make_envelope(
                "run.completed",
                {
                    "run_id": remote_run_id,
                    "status": "cancelled",
                    "result": result,
                    "receipt_digest": digest,
                },
                message_id=uuid5(
                    NAMESPACE_URL,
                    f"{PROTOCOL}:cancelled:{remote_run_id}:{digest}",
                ),
            ),
            remote_run_id=remote_run_id,
        )
        self.store.update_run(
            remote_run_id,
            status="cancelled",
            result_digest=digest,
        )

    def _sync_runs(self, websocket: Any) -> None:
        for remote in self.store.list_active_runs():
            remote_run_id = str(remote["remote_run_id"])
            local_run_id = str(remote["local_run_id"])
            try:
                snapshot = self.runtime.snapshot(local_run_id)
            except KeyError:
                self.store.update_run(
                    remote_run_id,
                    status="failed",
                    last_error="Local Run no longer exists",
                )
                continue
            steps = snapshot.get("steps")
            if isinstance(steps, list):
                cursor = int(remote["last_sent_sequence"])
                for step in steps:
                    sequence = int(step.get("sequence") or 0)
                    if sequence <= cursor:
                        continue
                    projection = project_agent_step(step)
                    envelope = make_envelope(
                        "run.event",
                        {
                            "run_id": remote_run_id,
                            "event_id": str(
                                uuid5(
                                    NAMESPACE_URL,
                                    f"{PROTOCOL}:event:{remote_run_id}:{sequence}",
                                )
                            ),
                            "event": projection,
                        },
                        message_id=uuid5(
                            NAMESPACE_URL,
                            f"{PROTOCOL}:event-message:{remote_run_id}:{sequence}",
                        ),
                    )
                    self._queue_and_send(
                        websocket,
                        envelope,
                        remote_run_id=remote_run_id,
                    )
                    cursor = sequence
                    self.store.update_run(
                        remote_run_id,
                        status="running",
                        last_sent_sequence=cursor,
                    )
            run = snapshot.get("run")
            run = run if isinstance(run, Mapping) else {}
            status = str(run.get("status") or "")
            if status not in _TERMINAL_RUN_STATUSES:
                continue
            result = project_run_result(snapshot)
            digest = payload_digest(result)
            self._queue_and_send(
                websocket,
                make_envelope(
                    "receipt.committed",
                    {
                        "run_id": remote_run_id,
                        "receipt_digest": digest,
                        "status": status,
                        "projection": result,
                    },
                    message_id=uuid5(
                        NAMESPACE_URL,
                        f"{PROTOCOL}:receipt:{remote_run_id}:{digest}",
                    ),
                ),
                remote_run_id=remote_run_id,
            )
            self._queue_and_send(
                websocket,
                make_envelope(
                    "run.completed",
                    {
                        "run_id": remote_run_id,
                        "status": self._remote_terminal_status(status),
                        "result": result,
                        "receipt_digest": digest,
                    },
                    message_id=uuid5(
                        NAMESPACE_URL,
                        f"{PROTOCOL}:completed:{remote_run_id}:{digest}",
                    ),
                ),
                remote_run_id=remote_run_id,
            )
            self.store.update_run(
                remote_run_id,
                status=self._remote_terminal_status(status),
                result_digest=digest,
            )

    @staticmethod
    def _remote_terminal_status(status: str) -> str:
        if status == "cancelled":
            return "cancelled"
        if status == "failed":
            return "failed"
        return "completed"

    def _send_ack(
        self,
        websocket: Any,
        received_message_id: str,
        *,
        accepted: bool,
        error: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "message_id": received_message_id,
            "accepted": accepted,
        }
        if error:
            payload["error"] = error[:500]
        self._send(websocket, make_envelope("message.ack", payload))

    def _replay_outbox(
        self,
        websocket: Any,
        *,
        skip_message_id: str | None = None,
    ) -> None:
        for envelope in self.store.pending_outbox():
            if str(envelope["message_id"]) == skip_message_id:
                continue
            self._send(websocket, envelope)
            self.store.mark_outbound_attempt(str(envelope["message_id"]))

    def _queue_and_send(
        self,
        websocket: Any,
        envelope: Mapping[str, object],
        *,
        remote_run_id: str | None = None,
    ) -> None:
        self.store.enqueue_outbound(envelope, remote_run_id=remote_run_id)
        self._send(websocket, envelope)
        self.store.mark_outbound_attempt(str(envelope["message_id"]))

    @staticmethod
    def _send(websocket: Any, envelope: Mapping[str, object]) -> None:
        websocket.send(json.dumps(dict(envelope), ensure_ascii=False, separators=(",", ":")))
