from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import shutil
import time
from typing import Any, Callable

from .app_server import CodexAppServerClient, CodexAppServerError
from .models import (
    ApprovalDecision,
    ApprovalRequest,
    EngineEvent,
    EnginePolicy,
    ThreadBinding,
    TurnOutcome,
)
from .protocol import (
    approval_from_message,
    assistant_delta,
    canonical_digest,
    changed_paths_from_event,
    event_from_notification,
)


@dataclass
class _Session:
    run_id: str
    client: CodexAppServerClient
    binding: ThreadBinding
    policy: EnginePolicy
    turn_id: str | None = None
    events: list[EngineEvent] = field(default_factory=list)
    message_parts: list[str] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    changed_paths: list[str] = field(default_factory=list)
    pending_approval: ApprovalRequest | None = None
    completed_status: str | None = None
    error: str | None = None


class CodexSessionManager:
    """Recoverable mapping between LightHouse Agent Runs and Codex v2 threads."""

    def __init__(
        self,
        *,
        binary: str = "codex",
        model: str | None = None,
        client_factory: Callable[..., CodexAppServerClient] | None = None,
    ):
        self.binary = binary
        self.model = model or None
        self.client_factory = client_factory or CodexAppServerClient
        self.sessions: dict[str, _Session] = {}

    def available(self) -> bool:
        return bool(shutil.which(self.binary) or Path(self.binary).is_file())

    def _new_client(self, cwd: str) -> CodexAppServerClient:
        return self.client_factory(binary=self.binary, cwd=cwd)

    @staticmethod
    def _extract_thread_id(value: dict[str, Any]) -> str:
        thread = value.get("thread") if isinstance(value.get("thread"), dict) else value
        thread_id = thread.get("id") if isinstance(thread, dict) else None
        if not thread_id:
            raise CodexAppServerError("Codex thread/start returned no thread id")
        return str(thread_id)

    @staticmethod
    def _extract_turn_id(value: dict[str, Any]) -> str | None:
        turn = value.get("turn") if isinstance(value.get("turn"), dict) else value
        turn_id = turn.get("id") if isinstance(turn, dict) else None
        return str(turn_id) if turn_id else None

    def start(
        self,
        *,
        run_id: str,
        task: str,
        cwd: str,
        policy: EnginePolicy,
        thread_id: str | None = None,
        ephemeral: bool = False,
    ) -> ThreadBinding:
        if run_id in self.sessions:
            return self.sessions[run_id].binding
        client = self._new_client(cwd)
        client.start()
        policy_params = policy.thread_params()
        if thread_id:
            started = client.thread_resume(thread_id, policy=policy_params)
        else:
            started = client.thread_start(
                cwd=cwd,
                model=self.model,
                policy=policy_params,
                ephemeral=ephemeral,
            )
        resolved_thread_id = self._extract_thread_id(started)
        binding = ThreadBinding(thread_id=resolved_thread_id, cwd=cwd)
        session = _Session(run_id=run_id, client=client, binding=binding, policy=policy)
        self.sessions[run_id] = session
        turn = client.turn_start(
            resolved_thread_id,
            task,
            cwd=cwd,
            model=self.model,
            policy=policy_params,
        )
        session.turn_id = self._extract_turn_id(turn)
        return binding

    def _consume_server_requests(self, session: _Session) -> None:
        while session.pending_approval is None:
            message = session.client.next_server_request(timeout=0)
            if message is None:
                return
            approval = approval_from_message(message)
            if approval is None:
                session.client.respond(message["id"], {"error": "unsupported client request"})
                continue
            session.pending_approval = approval

    def _consume_notifications(self, session: _Session) -> None:
        while True:
            message = session.client.next_notification(timeout=0)
            if message is None:
                return
            event = event_from_notification(message)
            session.events.append(event)
            delta = assistant_delta(event)
            if delta:
                session.message_parts.append(delta)
            for path in changed_paths_from_event(event):
                if path not in session.changed_paths:
                    session.changed_paths.append(path)
            if event.method == "turn/completed":
                turn = event.params.get("turn") if isinstance(event.params.get("turn"), dict) else event.params
                session.completed_status = str(turn.get("status") or "completed")
                usage = turn.get("usage") or event.params.get("usage")
                if isinstance(usage, dict):
                    session.usage = dict(usage)
                error = turn.get("error")
                if error:
                    session.error = str(error)
            elif event.method in {"turn/failed", "error"}:
                session.completed_status = "failed"
                session.error = str(event.params.get("error") or event.params.get("message") or "Codex turn failed")

    def poll(self, run_id: str, *, timeout: float = 0.25) -> TurnOutcome:
        session = self.sessions[run_id]
        deadline = time.monotonic() + max(0.0, timeout)
        while True:
            self._consume_server_requests(session)
            self._consume_notifications(session)
            if session.pending_approval or session.completed_status:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.02)
        return self._outcome(session)

    def wait_until_pause(self, run_id: str, *, timeout: float = 3600.0) -> TurnOutcome:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            outcome = self.poll(run_id, timeout=0.25)
            if outcome.approval or outcome.terminal:
                return outcome
        session = self.sessions[run_id]
        return TurnOutcome(
            status="failed",
            thread_id=session.binding.thread_id,
            turn_id=session.turn_id,
            message="Codex turn timed out",
            events=tuple(session.events),
            changed_paths=tuple(session.changed_paths),
            receipt_digest=canonical_digest([item.public_dict() for item in session.events]),
            error="timeout",
        )

    def approve(
        self,
        run_id: str,
        decision: ApprovalDecision | str,
        *,
        permissions: dict[str, Any] | None = None,
        scope: str | None = None,
    ) -> TurnOutcome:
        session = self.sessions[run_id]
        approval = session.pending_approval
        if approval is None:
            raise ValueError("Codex session has no pending approval")
        selected = ApprovalDecision(str(decision))
        if approval.method == "item/permissions/requestApproval":
            result = {"permissions": permissions or {}}
            if scope:
                result["scope"] = scope
        else:
            result = {"decision": selected.value}
        session.client.respond(approval.request_id, result)
        session.pending_approval = None
        return self.wait_until_pause(run_id)

    def steer(self, run_id: str, text: str) -> dict[str, Any]:
        session = self.sessions[run_id]
        return session.client.turn_steer(session.binding.thread_id, text)

    def interrupt(self, run_id: str) -> dict[str, Any]:
        session = self.sessions[run_id]
        if not session.turn_id:
            raise ValueError("Codex session has no active turn")
        return session.client.turn_interrupt(session.binding.thread_id, session.turn_id)

    def compact(self, run_id: str) -> dict[str, Any]:
        session = self.sessions[run_id]
        return session.client.thread_compact(session.binding.thread_id)

    def review(self, run_id: str) -> dict[str, Any]:
        session = self.sessions[run_id]
        return session.client.review_start(session.binding.thread_id)

    def fork(self, run_id: str, *, new_run_id: str, last_turn_id: str | None = None) -> ThreadBinding:
        session = self.sessions[run_id]
        result = session.client.thread_fork(
            session.binding.thread_id,
            last_turn_id=last_turn_id,
        )
        thread_id = self._extract_thread_id(result)
        binding = ThreadBinding(thread_id=thread_id, cwd=session.binding.cwd)
        self.sessions[new_run_id] = _Session(
            run_id=new_run_id,
            client=session.client,
            binding=binding,
            policy=session.policy,
        )
        return binding

    def close(self, run_id: str) -> None:
        session = self.sessions.pop(run_id, None)
        if session:
            session.client.close()

    def status(self, run_id: str) -> dict[str, Any]:
        session = self.sessions[run_id]
        return {
            "run_id": run_id,
            "thread_id": session.binding.thread_id,
            "turn_id": session.turn_id,
            "pending_approval": session.pending_approval.public_dict() if session.pending_approval else None,
            "completed_status": session.completed_status,
            "event_count": len(session.events),
            "changed_paths": list(session.changed_paths),
        }

    @staticmethod
    def _outcome(session: _Session) -> TurnOutcome:
        status = session.completed_status or ("approval_required" if session.pending_approval else "running")
        message = "".join(session.message_parts).strip()
        if not message and session.error:
            message = session.error
        if not message and status == "approval_required":
            message = "Codex requires approval to continue"
        projection = {
            "thread_id": session.binding.thread_id,
            "turn_id": session.turn_id,
            "status": status,
            "events": [item.public_dict() for item in session.events],
            "usage": session.usage,
            "changed_paths": session.changed_paths,
        }
        return TurnOutcome(
            status=status,
            thread_id=session.binding.thread_id,
            turn_id=session.turn_id,
            message=message,
            events=tuple(session.events),
            usage=session.usage,
            changed_paths=tuple(session.changed_paths),
            receipt_digest=canonical_digest(projection),
            approval=session.pending_approval,
            error=session.error,
        )
