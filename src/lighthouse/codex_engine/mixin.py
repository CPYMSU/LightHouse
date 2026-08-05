from __future__ import annotations

from typing import Any

from ..agent import _TERMINAL_AGENT_STATES
from ..models import AgentRunStatus, KernelMode
from .app_server import CodexAppServerError
from .models import (
    ApprovalDecision,
    ApprovalPolicy,
    CodeEngineMode,
    EnginePolicy,
    SandboxMode,
    normalize_engine_mode,
)
from .session import CodexSessionManager


class CodexEngineMixin:
    """Route coding Runs through native CodeFoundry, Codex v2, or both.

    The mixin sits before ``AdaptiveEngineeringMixin`` in the MRO. Non-coding
    work, unavailable Codex installations and native routes fall through
    unchanged. Every route decision and Codex event projection is written into
    the existing AgentRun ledger.
    """

    def _engine_mode(self) -> str:
        return normalize_engine_mode(getattr(self, "code_engine_mode", "auto"))

    def _codex_manager(self) -> CodexSessionManager:
        manager = getattr(self, "_lighthouse_codex_manager", None)
        if manager is None:
            manager = CodexSessionManager(
                binary=str(getattr(self, "codex_binary", "codex") or "codex"),
                model=str(getattr(self, "codex_model", "") or "") or None,
            )
            self._lighthouse_codex_manager = manager
        return manager

    def _codex_route(self, run) -> str:
        for step in reversed(self.repository.list_agent_steps(run.id)):
            if step.get("kind") == "code_engine.route_selected" and isinstance(step.get("payload"), dict):
                return str(step["payload"].get("mode") or "native")
        requested = self._engine_mode()
        if not getattr(self, "_is_coding_task", lambda _task: False)(run.task):
            return "native"
        manager = self._codex_manager()
        selected = requested
        if requested == CodeEngineMode.AUTO.value:
            selected = "codex" if manager.available() else "native"
        elif requested in {"codex", "hybrid", "shadow"} and not manager.available():
            selected = "native"
            self.repository.append_agent_step(
                run.id,
                "code_engine.unavailable",
                {"requested": requested, "reason": "Codex executable is not installed"},
            )
        self.repository.append_agent_step(
            run.id,
            "code_engine.route_selected",
            {
                "mode": selected,
                "requested": requested,
                "authoritative": selected in {"codex", "native"},
                "codex_available": manager.available(),
            },
        )
        return selected

    def _codex_cwd(self, run) -> str:
        workspace = self.repository.get_workspace(run.workspace_id)
        if not workspace.system_target_id:
            raise CodexAppServerError("coding workspace has no System target")
        target = self.kernel.repository.get_target(workspace.system_target_id)
        return str(target.config.get("default_cwd") or "").strip() or "/"

    def _codex_policy(self, *, advisory: bool, run) -> EnginePolicy:
        if advisory:
            return EnginePolicy(
                sandbox=SandboxMode.READ_ONLY,
                approval=ApprovalPolicy.NEVER,
                network_access=False,
            )
        auto = bool(run.auto_confirm)
        approval = ApprovalPolicy.NEVER if auto else ApprovalPolicy.ON_REQUEST
        return EnginePolicy(
            sandbox=SandboxMode.WORKSPACE_WRITE,
            approval=approval,
            writable_roots=(self._codex_cwd(run),),
            network_access=bool(getattr(self, "codex_network_access", False)),
        )

    def _record_codex_projection(self, run_id: str, outcome, *, route: str) -> None:
        seen = {
            str(step["payload"].get("receipt_digest") or "")
            for step in self.repository.list_agent_steps(run_id)
            if step.get("kind") == "codex.turn_projection" and isinstance(step.get("payload"), dict)
        }
        if outcome.receipt_digest in seen:
            return
        self.repository.append_agent_step(
            run_id,
            "codex.turn_projection",
            {
                "route": route,
                "status": outcome.status,
                "thread_id": outcome.thread_id,
                "turn_id": outcome.turn_id,
                "message": outcome.message,
                "usage": outcome.usage,
                "changed_paths": list(outcome.changed_paths),
                "receipt_digest": outcome.receipt_digest,
                "event_count": len(outcome.events),
                "error": outcome.error,
            },
        )

    def _run_codex(self, run, *, advisory: bool, route: str):
        manager = self._codex_manager()
        if run.id not in manager.sessions:
            task = run.task
            if advisory:
                task = (
                    "Read-only engineering advisory. Inspect the repository and produce a precise plan, "
                    "risks, likely files and validation strategy. Do not modify files or execute writes.\n\n"
                    + task
                )
            binding = manager.start(
                run_id=run.id,
                task=task,
                cwd=self._codex_cwd(run),
                policy=self._codex_policy(advisory=advisory, run=run),
                ephemeral=advisory,
            )
            self.repository.append_agent_step(
                run.id,
                "codex.thread_bound",
                {
                    "route": route,
                    "thread_id": binding.thread_id,
                    "cwd": binding.cwd,
                    "advisory": advisory,
                },
            )
        outcome = manager.wait_until_pause(run.id)
        self._record_codex_projection(run.id, outcome, route=route)
        return outcome

    def authorize_auto(self, run_id: str, *, actor: str):
        manager = getattr(self, "_lighthouse_codex_manager", None)
        if manager is not None and run_id in manager.sessions:
            session = manager.sessions[run_id]
            if session.pending_approval is not None:
                run = self.repository.get_agent_run(run_id)
                if run.actor != actor:
                    raise PermissionError("only the agent run actor may authorize Codex")
                self.repository.update_agent_run(
                    run_id,
                    auto_confirm=True,
                    auto_scope={
                        "version": 3,
                        "engine": "codex",
                        "thread_id": session.binding.thread_id,
                        "scope": "session",
                    },
                    status=AgentRunStatus.RUNNING,
                    response_status="pending",
                )
                outcome = manager.approve(run_id, ApprovalDecision.ACCEPT_FOR_SESSION)
                self._record_codex_projection(run_id, outcome, route="codex")
                return self._project_codex_authoritative(run, outcome)
        return super().authorize_auto(run_id, actor=actor)

    def _project_codex_authoritative(self, run, outcome):
        if outcome.approval is not None:
            self.repository.append_agent_step(
                run.id,
                "codex.approval_required",
                outcome.approval.public_dict(),
            )
            self.repository.update_agent_run(
                run.id,
                status=AgentRunStatus.AWAITING_CONFIRMATION,
                final_message=outcome.message,
                response_status="permission_required",
                goal_status="in_progress",
            )
            return self.snapshot(run.id)
        status_map = {
            "completed": AgentRunStatus.SUCCEEDED,
            "succeeded": AgentRunStatus.SUCCEEDED,
            "interrupted": AgentRunStatus.CANCELLED,
            "cancelled": AgentRunStatus.CANCELLED,
            "failed": AgentRunStatus.FAILED,
        }
        status = status_map.get(outcome.status, AgentRunStatus.RUNNING)
        if status is AgentRunStatus.RUNNING:
            return self.snapshot(run.id)
        self.repository.append_agent_step(
            run.id,
            "codex.route_completed",
            {
                "status": outcome.status,
                "message": outcome.message,
                "receipt_digest": outcome.receipt_digest,
                "changed_paths": list(outcome.changed_paths),
                "usage": outcome.usage,
            },
        )
        self.repository.update_agent_run(
            run.id,
            status=status,
            final_message=outcome.message or f"Codex turn {outcome.status}",
            pending_operation_id=None,
            execution_status="succeeded" if status is AgentRunStatus.SUCCEEDED else "not_completed",
            response_status="succeeded" if status is AgentRunStatus.SUCCEEDED else outcome.status,
            goal_status="completed" if status is AgentRunStatus.SUCCEEDED else "not_completed",
            warning=outcome.error,
            auto_confirm=False,
            auto_scope={},
        )
        return self.snapshot(run.id)

    def advance(self, run_id: str):
        run = self.repository.get_agent_run(run_id)
        if run.status in _TERMINAL_AGENT_STATES:
            return self.snapshot(run_id)
        route = self._codex_route(run)
        if route == "codex":
            try:
                outcome = self._run_codex(run, advisory=False, route=route)
            except Exception as exc:
                self.repository.append_agent_step(
                    run_id,
                    "codex.route_failed",
                    {"error": str(exc), "error_type": type(exc).__name__},
                )
                self.repository.update_agent_run(
                    run_id,
                    status=AgentRunStatus.FAILED,
                    final_message=f"Codex engine failed: {exc}",
                    response_status="codex_failed",
                    goal_status="not_completed",
                    warning=str(exc),
                )
                return self.snapshot(run_id)
            return self._project_codex_authoritative(run, outcome)
        if route in {"hybrid", "shadow"}:
            completed_kind = f"code_engine.{route}_advisory_completed"
            if not any(step.get("kind") == completed_kind for step in self.repository.list_agent_steps(run_id)):
                try:
                    outcome = self._run_codex(run, advisory=True, route=route)
                    self.repository.append_agent_step(
                        run_id,
                        completed_kind,
                        {
                            "thread_id": outcome.thread_id,
                            "message": outcome.message,
                            "receipt_digest": outcome.receipt_digest,
                            "status": outcome.status,
                            "authoritative": False,
                        },
                    )
                    self._codex_manager().close(run_id)
                except Exception as exc:
                    self.repository.append_agent_step(
                        run_id,
                        f"code_engine.{route}_advisory_failed",
                        {"error": str(exc), "error_type": type(exc).__name__},
                    )
            return super().advance(run_id)
        return super().advance(run_id)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        value = super().snapshot(run_id)
        manager = getattr(self, "_lighthouse_codex_manager", None)
        value["code_engine"] = {
            "mode": self._engine_mode(),
            "codex_available": self._codex_manager().available(),
            "session": manager.status(run_id) if manager is not None and run_id in manager.sessions else None,
        }
        return value
