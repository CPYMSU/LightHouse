from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import re
from threading import Thread
from typing import Any

from .agent import AgentRuntime, _TERMINAL_AGENT_STATES
from .code_foundry.agent_provider import AgentProviderCodeAdapter
from .code_foundry.brief import CodeBriefCompiler
from .code_foundry.durable_run import AgentStoreCodeRunSink
from .code_foundry.loop import CodeFoundryLoop, CodeRunOutcome
from .code_foundry.models import CodeAction, CodeObservation, CodeResultStatus
from .code_foundry.runtime import CodeRuntime, KernelCodeActionExecutor
from .code_foundry.tools import CodeActionRegistry
from .models import (
    AgentRunStatus,
    ConfirmationMode,
    KernelMode,
    OperationStatus,
)
from .provider import AgentProtocolError, ModelNotConfiguredError, OpenAICompatibleProvider


_CODE_PATCH_CAPABILITY = "system.file.patch.v1"
_DIFF_CAPABILITY = "system.git.diff.v1"
_VALIDATION_CAPABILITIES = {
    "system.test.run.v1",
    "system.file.read.v1",
    "system.project.context.v1",
}
_SHADOW_NAME = re.compile(
    r"(?:^|/)(?:[^/]+(?:_new|_copy|_fixed|_final|_v\d+)|index(?:\s+copy|-copy|_copy)\.[^/]+)$",
    re.IGNORECASE,
)
_CODE_TASK_TERMS = re.compile(
    r"\b(?:code|coding|implement|implementation|fix|bug|debug|refactor|test|tests|"
    r"function|class|module|api|repository|repo|project|script|build|compile|deploy)\b|"
    r"(?:程式|代码|編程|编程|實作|实现|修復|修复|除錯|调试|重構|重构|測試|测试|函式|函数|模組|模块|專案|项目|倉庫|仓库|部署)",
    re.IGNORECASE,
)


class _ReadOnlyShadowExecutor:
    """Observe a CodeFoundry candidate without permitting a duplicate patch.

    ``shadow`` is deliberately non-authoritative. It exercises the same brief,
    model adapter, tool vocabulary, and read operations, but records a blocked
    patch rather than mutating the active workspace a second time.
    """

    def __init__(self, delegate: KernelCodeActionExecutor):
        self.delegate = delegate

    async def execute(self, action: CodeAction) -> CodeObservation:
        if action.mutates_workspace:
            return CodeObservation(
                id=f"shadow-blocked:{action.id}",
                action_id=action.id,
                kind=action.kind,
                ok=False,
                payload={
                    "shadow": True,
                    "blocked": "workspace mutation withheld in CodeFoundry shadow mode",
                    "proposed_arguments": dict(action.arguments),
                },
            )
        observation = await self.delegate.execute(action)
        return CodeObservation(
            id=observation.id,
            action_id=observation.action_id,
            kind=observation.kind,
            ok=observation.ok,
            payload={**observation.payload, "shadow": True},
            started_at=observation.started_at,
            completed_at=observation.completed_at,
        )


@dataclass(frozen=True)
class CompletionReview:
    status: str
    blockers: tuple[str, ...] = ()
    guidance: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_fingerprint: str = ""

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "blockers": list(self.blockers),
            "guidance": list(self.guidance),
            "warnings": list(self.warnings),
            "evidence_fingerprint": self.evidence_fingerprint,
        }


def _run_async_safely(awaitable):
    """Run a CodeFoundry coroutine from sync API and test entry points.

    The public runtime is synchronous today. API handlers normally have no
    running loop, while notebook/async hosts can. The small thread fallback
    preserves the same durable run contract in both cases.
    """

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)

    outcome: dict[str, Any] = {}
    failure: list[BaseException] = []

    def run() -> None:
        try:
            outcome["value"] = asyncio.run(awaitable)
        except BaseException as exc:  # propagate the original provider/runtime error
            failure.append(exc)

    worker = Thread(target=run, name="lighthouse-code-foundry", daemon=False)
    worker.start()
    worker.join()
    if failure:
        raise failure[0]
    return outcome["value"]


class AdaptiveEngineeringMixin:
    """Adaptive engineering policy layered over the existing autonomous runtime.

    Planning, delegation, file choice and validation depth remain main-AI judgments.
    The mixin only makes execution truth, Run-wide Auto authority, no-progress
    behavior and completion evidence durable and observable.
    """

    minimum_soft_steps = 48
    hard_step_limit = 256
    extension_size = 24

    def _code_foundry_mode(self) -> str:
        value = str(getattr(self, "code_foundry_mode", "off") or "off").strip().lower()
        return value if value in {"off", "shadow", "on"} else "off"

    @staticmethod
    def _is_coding_task(task: str) -> bool:
        return bool(_CODE_TASK_TERMS.search(str(task or "")))

    def _code_foundry_route(self, run) -> str:
        """Choose one durable route for the run instead of re-deciding per turn."""

        for step in reversed(self.repository.list_agent_steps(run.id)):
            if step.get("kind") != "code_foundry.route_selected":
                continue
            payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
            selected = str(payload.get("mode") or "off")
            return selected if selected in {"shadow", "on"} else "off"

        requested = self._code_foundry_mode()
        if requested == "off":
            return "off"
        if run.mode not in {KernelMode.AUTO, KernelMode.SYSTEM}:
            self.repository.append_agent_step(
                run.id,
                "code_foundry.route_skipped",
                {
                    "requested_mode": requested,
                    "reason": f"run kernel mode {run.mode.value} does not expose the native coding tools",
                },
            )
            return "off"
        if not self._is_coding_task(run.task):
            self.repository.append_agent_step(
                run.id,
                "code_foundry.route_skipped",
                {
                    "requested_mode": requested,
                    "reason": "task did not match the coding-route classifier",
                },
            )
            return "off"

        self.repository.append_agent_step(
            run.id,
            "code_foundry.route_selected",
            {
                "mode": requested,
                "authoritative": requested == "on",
                "reason": "feature flag and coding-route classifier matched",
            },
        )
        return requested

    def _code_foundry_project_context(self, run_id: str) -> dict[str, Any]:
        for step in reversed(self.repository.list_agent_steps(run_id)):
            if step.get("kind") != "project_context":
                continue
            payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
            receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
            result = receipt.get("result") if isinstance(receipt.get("result"), dict) else {}
            if result:
                return dict(result)
            if isinstance(payload.get("result"), dict):
                return dict(payload["result"])
        return {}

    def _code_foundry_brief(self, run) -> Any:
        self._ensure_project_context(run.id)
        project_context = self._code_foundry_project_context(run.id)
        try:
            state = self._model_state(run.id)
        except Exception:
            state = {}
        cognitive = state.get("context_intelligence") if isinstance(state, dict) else {}
        if not isinstance(cognitive, dict):
            continuity = state.get("cognitive_continuity") if isinstance(state, dict) else {}
            cognitive = continuity.get("state") if isinstance(continuity, dict) else {}
        workspace = self.repository.get_workspace(run.workspace_id)
        workspace_config = workspace.config if isinstance(workspace.config, dict) else {}
        user_inputs = [
            str(step["payload"].get("message") or "").strip()
            for step in self.repository.list_agent_steps(run.id)
            if step.get("kind") == "user_input" and isinstance(step.get("payload"), dict)
        ]
        task = run.task
        if user_inputs:
            task = f"{task}\n\nLatest user direction: {user_inputs[-1]}"
        return CodeBriefCompiler().compile(
            task=task,
            project_context=project_context,
            cognitive_context=cognitive,
            test_commands=workspace_config.get("test_commands") or (),
        )

    def _execute_code_foundry(self, run, *, route: str) -> CodeRunOutcome:
        brief = self._code_foundry_brief(run)
        registry = CodeActionRegistry()
        executor: Any = KernelCodeActionExecutor(
            self.kernel,
            workspace_id=run.workspace_id,
            actor=run.actor,
            registry=registry,
            mode=run.mode,
            # CodeFoundry owns an evidence-gated, production coding loop. The
            # user selected autonomous production execution for this program.
            auto_confirm=True,
        )
        if route == "shadow":
            executor = _ReadOnlyShadowExecutor(executor)
        max_turns = min(
            self.hard_step_limit,
            max(1, int(run.max_steps)),
            8 if route == "shadow" else self.hard_step_limit,
        )
        loop = CodeFoundryLoop(
            model=AgentProviderCodeAdapter(self.provider, registry=registry),
            runtime=CodeRuntime(executor),
            registry=registry,
            max_turns=max_turns,
            event_sink=AgentStoreCodeRunSink(self.repository, run.id),
        )
        self.repository.append_agent_step(
            run.id,
            "code_foundry.route_started",
            {
                "mode": route,
                "authoritative": route == "on",
                "max_turns": max_turns,
                "brief": brief.public_dict(),
            },
        )
        return _run_async_safely(loop.run(brief))

    def _project_code_foundry_result(self, run, outcome: CodeRunOutcome) -> dict[str, Any]:
        result = outcome.result
        statuses = {
            CodeResultStatus.VERIFIED: (AgentRunStatus.SUCCEEDED, "code_foundry_verified", "completed", None),
            CodeResultStatus.NEEDS_INPUT: (AgentRunStatus.WAITING_INPUT, "code_foundry_needs_input", "waiting_input", None),
            CodeResultStatus.FAILED: (AgentRunStatus.FAILED, "code_foundry_failed", "blocked", result.summary),
            CodeResultStatus.UNVERIFIED: (
                AgentRunStatus.PARTIALLY_COMPLETED,
                "code_foundry_unverified",
                "incomplete",
                result.summary,
            ),
        }
        status, response_status, goal_status, warning = statuses[result.status]
        self.repository.append_agent_step(
            run.id,
            "code_foundry.route_completed",
            {
                "mode": "on",
                "authoritative": True,
                "status": result.status.value,
                "summary": result.summary,
                "changed_paths": list(result.changed_paths),
                "evidence_ids": list(result.evidence_ids),
                "blockers": list(result.blockers),
                "turns": outcome.turns,
            },
        )
        self.repository.update_agent_run(
            run.id,
            status=status,
            current_step=min(self.hard_step_limit, int(run.current_step) + outcome.turns),
            final_message=result.summary,
            pending_operation_id=None,
            execution_status="succeeded" if result.status is CodeResultStatus.VERIFIED else "not_verified",
            response_status=response_status,
            goal_status=goal_status,
            warning=warning,
            auto_confirm=False,
            auto_scope={},
        )
        return self._engineering_sync(run.id, self.snapshot(run.id))

    def start(
        self,
        *,
        task: str,
        workspace_id: str,
        actor: str,
        mode: KernelMode = KernelMode.AUTO,
        max_steps: int = 48,
        auto_confirm: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        requested = max(1, int(max_steps))
        soft_limit = min(max(requested, self.minimum_soft_steps), 64)
        return super().start(
            task=task,
            workspace_id=workspace_id,
            actor=actor,
            mode=mode,
            max_steps=soft_limit,
            auto_confirm=auto_confirm,
            **kwargs,
        )

    def authorize_auto(self, run_id: str, *, actor: str) -> dict[str, Any]:
        run = self.repository.get_agent_run(run_id)
        if run.actor != actor:
            raise PermissionError("only the agent run actor may authorize Auto Mode")
        if run.status != AgentRunStatus.AWAITING_CONFIRMATION or not run.pending_operation_id:
            raise ValueError("agent run has no pending operation to authorize")

        pending = self.kernel.snapshot(run.pending_operation_id)
        operation = pending["operation"]
        workspace = self.repository.get_workspace(run.workspace_id)
        target_ids = [
            target_id
            for target_id in (
                workspace.data_target_id,
                workspace.system_target_id,
                getattr(workspace, "desktop_target_id", None),
            )
            if target_id
        ]
        allowed_roots_by_target: dict[str, list[str]] = {}
        for target_id in target_ids:
            target = self.kernel.repository.get_target(target_id)
            allowed_roots_by_target[target_id] = list(target.config.get("allowed_roots") or [])

        scope = {
            "version": 2,
            "run_wide": True,
            "workspace_id": run.workspace_id,
            "actor": run.actor,
            "target_ids": target_ids,
            "allowed_capabilities": ["*"],
            "allowed_kernels": [mode.value for mode in KernelMode if mode is not KernelMode.AUTO],
            "allowed_roots_by_target": allowed_roots_by_target,
            "granted_from_operation_id": operation["id"],
            "ends_on": ["terminal", "cancelled", "user_disabled"],
        }
        self.repository.append_agent_step(
            run_id,
            "auto_scope_granted",
            {
                "actor": actor,
                "operation_id": operation["id"],
                "scope": scope,
                "message": "One confirmation authorizes all subsequent governed operations in this Run.",
            },
        )
        self.repository.update_agent_run(
            run_id,
            auto_confirm=True,
            auto_scope=scope,
            status=AgentRunStatus.RUNNING,
            response_status="pending",
        )
        confirmed = self.kernel.confirm(operation["id"], actor=actor)
        self.repository.append_agent_step(
            run_id,
            "auto_confirmation",
            {
                "operation_id": operation["id"],
                "actor": actor,
                "scope": scope,
                "initial": True,
                "status": confirmed["operation"]["status"],
            },
        )
        return self.advance(run_id)

    @staticmethod
    def _auto_scope_allows(run, operation: dict[str, Any]) -> bool:
        if not run.auto_confirm or not isinstance(run.auto_scope, dict):
            return False
        scope = run.auto_scope
        value = operation.get("operation") or {}
        if not scope.get("run_wide"):
            return AgentRuntime._auto_scope_allows(run, operation)
        target_ids = set(scope.get("target_ids") or [])
        allowed_capabilities = set(scope.get("allowed_capabilities") or [])
        allowed_kernels = set(scope.get("allowed_kernels") or [])
        return (
            value.get("workspace_id") == scope.get("workspace_id")
            and value.get("target_id") in target_ids
            and ("*" in allowed_capabilities or value.get("capability") in allowed_capabilities)
            and (not allowed_kernels or value.get("kernel") in allowed_kernels)
        )

    def _dispatch_tool(self, run, decision, step_number: int):
        operation = super()._dispatch_tool(run, decision, step_number)
        if not operation:
            return operation
        value = operation.get("operation") or {}
        if value.get("status") != OperationStatus.AWAITING_CONFIRMATION.value:
            return operation
        if not self._auto_scope_allows(run, operation):
            return operation
        capability = self.kernel.registry.get(str(value.get("capability") or ""))
        if capability.confirmation not in {ConfirmationMode.EXPLICIT, ConfirmationMode.PASSKEY}:
            return operation
        self.repository.append_agent_step(
            run.id,
            "auto_confirmation",
            {
                "step": step_number,
                "operation_id": value["id"],
                "actor": run.actor,
                "scope": run.auto_scope,
                "initial": False,
            },
        )
        return self.kernel.confirm(value["id"], actor=run.actor)

    def advance(self, run_id: str) -> dict[str, Any]:
        run = self.repository.get_agent_run(run_id)
        if run.status in _TERMINAL_AGENT_STATES:
            return self._engineering_sync(run_id, self.snapshot(run_id))
        if run.status == AgentRunStatus.WAITING_INPUT:
            return self._engineering_sync(run_id, self.snapshot(run_id))

        route = self._code_foundry_route(run)
        if route == "on":
            outcome = self._execute_code_foundry(run, route=route)
            return self._project_code_foundry_result(run, outcome)
        if route == "shadow" and not any(
            step.get("kind") == "code_foundry.shadow_completed"
            for step in self.repository.list_agent_steps(run_id)
        ):
            outcome = self._execute_code_foundry(run, route=route)
            result = outcome.result
            self.repository.append_agent_step(
                run_id,
                "code_foundry.shadow_completed",
                {
                    "authoritative": False,
                    "status": result.status.value,
                    "summary": result.summary,
                    "changed_paths": list(result.changed_paths),
                    "evidence_ids": list(result.evidence_ids),
                    "blockers": list(result.blockers),
                    "turns": outcome.turns,
                    "note": "Shadow mode withheld CodeFoundry workspace mutations; the legacy result remains authoritative.",
                },
            )

        if run.pending_operation_id:
            pending = self.kernel.snapshot(run.pending_operation_id)
            status = OperationStatus(pending["operation"]["status"])
            if status == OperationStatus.AWAITING_CONFIRMATION:
                if self._auto_scope_allows(run, pending):
                    pending = self.kernel.confirm(run.pending_operation_id, actor=run.actor)
                    status = OperationStatus(pending["operation"]["status"])
                else:
                    self.repository.update_agent_run(
                        run_id,
                        status=AgentRunStatus.AWAITING_CONFIRMATION,
                    )
                    return self._engineering_sync(run_id, self.snapshot(run_id))
            if status == OperationStatus.RUNNING:
                return self._engineering_sync(run_id, self.snapshot(run_id))
            observation = self._operation_observation(pending)
            self.repository.append_agent_step(run_id, "observation", observation)
            self._record_execution_outcome(run_id, observation)
            run = self.repository.update_agent_run(
                run_id,
                status=AgentRunStatus.RUNNING,
                pending_operation_id=None,
            )

        if run.status == AgentRunStatus.CREATED:
            run = self.repository.update_agent_run(run_id, status=AgentRunStatus.RUNNING)

        self._ensure_project_context(run_id)
        run = self.repository.get_agent_run(run_id)

        while run.current_step < self.hard_step_limit:
            effective_limit = self._effective_step_limit(run_id, run.max_steps)
            if run.current_step >= effective_limit:
                if not self._may_extend(run_id):
                    return self._engineering_step_limit(run_id, effective_limit)
                next_limit = min(self.hard_step_limit, effective_limit + self.extension_size)
                self.repository.append_agent_step(
                    run_id,
                    "budget_extended",
                    {
                        "previous_limit": effective_limit,
                        "new_limit": next_limit,
                        "reason": "verified progress continues; budget is advisory",
                    },
                )

            state = self._model_state(run_id)
            try:
                decision = self.provider.decide(
                    system_prompt=self._system_prompt(run),
                    state=state,
                )
            except ModelNotConfiguredError:
                raise
            except AgentProtocolError as exc:
                next_step = run.current_step + 1
                self.repository.append_agent_step(
                    run_id,
                    "protocol_error",
                    {"error": str(exc), "step": next_step},
                )
                run = self.repository.update_agent_run(
                    run_id,
                    current_step=next_step,
                    status=AgentRunStatus.RUNNING,
                    response_status="protocol_error",
                )
                continue
            except Exception as exc:
                return self._engineering_sync(run_id, self._provider_failed(run_id, exc))

            next_step = run.current_step + 1
            self.repository.append_agent_step(
                run_id,
                "decision",
                {"step": next_step, **decision.public_dict()},
            )
            run = self.repository.update_agent_run(
                run_id,
                current_step=next_step,
                status=AgentRunStatus.RUNNING,
            )

            if decision.kind == "memory_expand":
                depth = str((decision.arguments or {}).get("depth") or "focused")
                self.repository.append_agent_step(
                    run_id,
                    "memory_context_expanded",
                    {
                        "step": next_step,
                        "depth": depth,
                        "reason": decision.reason,
                        "source": "model_requested_progressive_memory",
                    },
                )
                continue

            if decision.kind == "final":
                self.repository.append_agent_step(
                    run_id,
                    "final_candidate",
                    {"message": decision.message, "reason": decision.reason, "step": next_step},
                )
                review = self._completion_review(run_id)
                self.repository.append_agent_step(run_id, "completion_review", review.public_dict())
                if review.status == "revise":
                    run = self.repository.update_agent_run(
                        run_id,
                        status=AgentRunStatus.RUNNING,
                        response_status="completion_review",
                        goal_status="in_progress",
                        warning="; ".join((*review.blockers, *review.guidance)) or None,
                    )
                    continue
                successful = self._successful_observations(run_id)
                warning = "; ".join(review.warnings) or None
                self.repository.append_agent_step(
                    run_id,
                    "run_completed",
                    {
                        "message": decision.message,
                        "step": next_step,
                        "completion_review": review.public_dict(),
                    },
                )
                self.repository.update_agent_run(
                    run_id,
                    status=(
                        AgentRunStatus.COMPLETED_WITH_WARNING
                        if warning
                        else AgentRunStatus.SUCCEEDED
                    ),
                    final_message=decision.message,
                    pending_operation_id=None,
                    execution_status="succeeded" if successful else "not_required",
                    response_status="succeeded",
                    goal_status="completed",
                    warning=warning,
                    auto_confirm=False,
                    auto_scope={},
                )
                return self._engineering_sync(run_id, self.snapshot(run_id))

            if decision.kind == "ask":
                self.repository.append_agent_step(
                    run_id,
                    "input_required",
                    {"message": decision.message, "step": next_step},
                )
                self.repository.update_agent_run(
                    run_id,
                    status=AgentRunStatus.WAITING_INPUT,
                    final_message=decision.message,
                    response_status="succeeded",
                    goal_status="waiting_input",
                    auto_confirm=False,
                    auto_scope={},
                )
                return self._engineering_sync(run_id, self.snapshot(run_id))

            tool_result = self._dispatch_tool(run, decision, next_step)
            if tool_result is None:
                run = self.repository.get_agent_run(run_id)
                continue
            if tool_result["operation"]["status"] == OperationStatus.AWAITING_CONFIRMATION.value:
                self.repository.update_agent_run(
                    run_id,
                    status=AgentRunStatus.AWAITING_CONFIRMATION,
                    pending_operation_id=tool_result["operation"]["id"],
                    response_status="permission_required",
                )
                return self._engineering_sync(run_id, self.snapshot(run_id))
            observation = self._operation_observation(tool_result)
            self.repository.append_agent_step(run_id, "observation", observation)
            self._record_execution_outcome(run_id, observation)
            run = self.repository.get_agent_run(run_id)

        return self._engineering_step_limit(run_id, self.hard_step_limit)

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        steps = self.repository.list_agent_steps(run_id)
        state["engineering"] = {
            "operating_principle": (
                "Existing code is the primary implementation and source of truth. Improve the active "
                "implementation before creating a parallel replacement, while retaining freedom to "
                "extract or replace code when evidence shows that is materially cleaner and safer."
            ),
            "adaptive_loop": (
                "Gather enough context, act, verify with tools, review the diff, and revise when evidence "
                "changes. Planning and delegation are optional working strategies, not mandatory phases."
            ),
            "completion_rule": (
                "A final answer is a candidate until objective failures and change evidence are reviewed."
            ),
            "auto_scope": (state.get("run") or {}).get("auto_scope") or {},
            "recent_change_intent": self._recent_change_intent(steps),
            "latest_completion_review": self._latest_step_payload(steps, "completion_review"),
            "shadow_implementation_candidates": self._shadow_candidates(steps),
            "effective_step_limit": self._effective_step_limit(
                run_id,
                int((state.get("run") or {}).get("max_steps") or self.minimum_soft_steps),
            ),
            "hard_step_limit": self.hard_step_limit,
        }
        return state

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "Operate as an adaptive senior engineer, not a code generator. Treat the existing repository, "
            "its active entry points, tests, contracts and current diff as primary evidence. Prefer editing, "
            "extending or carefully refactoring the active implementation over creating parallel copies. "
            "Do not create _new, _fixed, _v2 or copy variants merely to avoid understanding existing code. "
            "A new module or rewrite is welcome when it creates a materially cleaner boundary; explain the "
            "reason through the durable decision and preserve or intentionally migrate public behavior. "
            "Choose investigation, planning, Agent delegation and validation depth according to uncertainty "
            "and risk. Small clear changes may be direct. Complex changes may use plans, Agents, Build Cells "
            "or Worktrees, and plans must remain revisable. Use tools continuously to inspect, change, run, "
            "test and review. Minimize architectural entropy rather than Token or tool usage. After the user "
            "grants Run-wide Auto once, continue through all attached Workspace targets and governed capability "
            "classes without asking again until the Run ends. A final response is only a candidate: resolve "
            "objective failures, inspect code changes and obtain validation proportional to the changed behavior. "
            + base
        )

    def _completion_review(self, run_id: str) -> CompletionReview:
        steps = self.repository.list_agent_steps(run_id)
        observations = [
            step
            for step in steps
            if step.get("kind") == "observation" and isinstance(step.get("payload"), dict)
        ]
        failed = [
            step
            for step in observations
            if isinstance(step["payload"].get("receipt"), dict)
            and step["payload"]["receipt"].get("ok") is False
        ]
        blockers: list[str] = []
        guidance: list[str] = []
        warnings: list[str] = []
        if failed:
            latest_failure = failed[-1]
            later_success = any(
                candidate.get("sequence", 0) > latest_failure.get("sequence", 0)
                and isinstance(candidate["payload"].get("receipt"), dict)
                and candidate["payload"]["receipt"].get("ok") is True
                for candidate in observations
            )
            if not later_success:
                blockers.append("the latest verified operation failed and has no later successful recovery evidence")

        patch_sequences = self._tool_decision_sequences(steps, _CODE_PATCH_CAPABILITY)
        if patch_sequences:
            last_patch = max(patch_sequences)
            successful_after = {
                str(step["payload"].get("capability") or "")
                for step in observations
                if step.get("sequence", 0) > last_patch
                and isinstance(step["payload"].get("receipt"), dict)
                and step["payload"]["receipt"].get("ok") is True
            }
            if _DIFF_CAPABILITY not in successful_after:
                guidance.append("inspect the resulting Git diff after the latest code patch")
            if not successful_after.intersection(_VALIDATION_CAPABILITIES):
                guidance.append("obtain validation proportional to the changed behavior after the latest patch")
            shadow = self._shadow_candidates(steps)
            if shadow:
                guidance.append(
                    "review possible parallel implementations and either integrate them into the active entry point "
                    "or preserve a clear migration rationale: " + ", ".join(shadow[:5])
                )

        fingerprint = json.dumps(
            {
                "blockers": blockers,
                "guidance": guidance,
                "last_observation": observations[-1].get("sequence") if observations else 0,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        previous_same = any(
            step.get("kind") == "completion_review"
            and isinstance(step.get("payload"), dict)
            and step["payload"].get("evidence_fingerprint") == fingerprint
            for step in steps[:-1]
        )
        if blockers:
            return CompletionReview(
                status="revise",
                blockers=tuple(blockers),
                guidance=tuple(guidance),
                evidence_fingerprint=fingerprint,
            )
        if guidance and not previous_same:
            return CompletionReview(
                status="revise",
                guidance=tuple(guidance),
                evidence_fingerprint=fingerprint,
            )
        if guidance:
            warnings.extend(guidance)
        return CompletionReview(
            status="pass_with_warning" if warnings else "pass",
            warnings=tuple(warnings),
            evidence_fingerprint=fingerprint,
        )

    def _engineering_step_limit(self, run_id: str, limit: int) -> dict[str, Any]:
        message = f"Engineering Run reached its adaptive limit of {limit} model steps without completion"
        successful = self._successful_observations(run_id)
        status = AgentRunStatus.PARTIALLY_COMPLETED if successful else AgentRunStatus.FAILED
        self.repository.append_agent_step(
            run_id,
            "run_warning" if successful else "run_failed",
            {"reason": "adaptive_step_limit", "message": message, "limit": limit},
        )
        self.repository.update_agent_run(
            run_id,
            status=status,
            final_message=self._receipt_fallback(successful, message) if successful else message,
            execution_status="succeeded" if successful else "not_completed",
            response_status="step_limit",
            goal_status="partially_completed" if successful else "not_completed",
            warning=message if successful else None,
            auto_confirm=False,
            auto_scope={},
        )
        return self._engineering_sync(run_id, self.snapshot(run_id))

    def _effective_step_limit(self, run_id: str, initial: int) -> int:
        limit = max(int(initial), self.minimum_soft_steps)
        for step in self.repository.list_agent_steps(run_id):
            if step.get("kind") == "budget_extended" and isinstance(step.get("payload"), dict):
                limit = max(limit, int(step["payload"].get("new_limit") or limit))
        return min(limit, self.hard_step_limit)

    def _may_extend(self, run_id: str) -> bool:
        steps = self.repository.list_agent_steps(run_id)
        recent = steps[-24:]
        successful = [
            step
            for step in recent
            if step.get("kind") == "observation"
            and isinstance(step.get("payload"), dict)
            and isinstance(step["payload"].get("receipt"), dict)
            and step["payload"]["receipt"].get("ok") is True
        ]
        decisions = [
            step["payload"]
            for step in recent
            if step.get("kind") == "decision" and isinstance(step.get("payload"), dict)
        ]
        signatures = {
            json.dumps(
                {
                    "kind": item.get("kind"),
                    "capability": item.get("capability"),
                    "arguments": item.get("arguments"),
                },
                sort_keys=True,
                default=str,
                separators=(",", ":"),
            )
            for item in decisions
        }
        return bool(successful) and len(signatures) >= max(1, len(decisions) // 3)

    def _engineering_sync(self, run_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        bridge = getattr(self, "memory_bridge", None)
        if bridge is not None:
            try:
                bridge.sync(run_id, snapshot)
            except Exception:
                pass
        return snapshot

    @staticmethod
    def _latest_step_payload(steps: list[dict[str, Any]], kind: str) -> dict[str, Any] | None:
        for step in reversed(steps):
            if step.get("kind") == kind and isinstance(step.get("payload"), dict):
                return step["payload"]
        return None

    @staticmethod
    def _tool_decision_sequences(steps: list[dict[str, Any]], capability: str) -> list[int]:
        return [
            int(step.get("sequence") or 0)
            for step in steps
            if step.get("kind") == "decision"
            and isinstance(step.get("payload"), dict)
            and step["payload"].get("kind") == "tool"
            and step["payload"].get("capability") == capability
        ]

    @staticmethod
    def _recent_change_intent(steps: list[dict[str, Any]]) -> dict[str, Any]:
        tools = [
            step["payload"]
            for step in steps[-24:]
            if step.get("kind") == "decision"
            and isinstance(step.get("payload"), dict)
            and step["payload"].get("kind") == "tool"
        ]
        return {
            "recent_capabilities": [str(item.get("capability") or "") for item in tools[-8:]],
            "prefer": "patch_existing_or_extend_active_entrypoint",
            "new_files_require": "clear module boundary or migration rationale",
        }

    @staticmethod
    def _shadow_candidates(steps: list[dict[str, Any]]) -> list[str]:
        values: list[str] = []
        for step in steps:
            if step.get("kind") != "decision" or not isinstance(step.get("payload"), dict):
                continue
            payload = step["payload"]
            if payload.get("capability") != _CODE_PATCH_CAPABILITY:
                continue
            arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
            patch = str(arguments.get("patch") or "")
            for line in patch.splitlines():
                if not line.startswith("+++ b/"):
                    continue
                path = line[6:].strip()
                if path != "/dev/null" and _SHADOW_NAME.search(path):
                    values.append(path)
        return sorted(set(values))


class StructuredOpenAICompatibleProvider(OpenAICompatibleProvider):
    """Compact model state by semantic sections, never raw head/tail slicing."""

    def _bounded_json(self, value: Any) -> str:
        text = self._serialize(value)
        if len(text) <= self.max_state_chars:
            return text

        source = value if isinstance(value, dict) else {"value": value}
        compact: dict[str, Any] = {
            "context_compacted": True,
            "run": source.get("run"),
            "workspace": source.get("workspace"),
            "usage_context": source.get("usage_context"),
            "engineering": source.get("engineering"),
            "coordination_advice": source.get("coordination_advice"),
            "data_worlds": self._select_mapping(
                source.get("data_worlds"),
                ("available", "bindings", "resources", "error", "error_type"),
            ),
            "memory": self._select_mapping(
                source.get("memory"),
                (
                    "active_task",
                    "recent_turns",
                    "conversation_summary",
                    "verified_facts",
                    "uncertainties",
                    "relevant_files",
                    "recent_locators",
                ),
            ),
            "context_intelligence": self._select_mapping(
                source.get("context_intelligence"),
                (
                    "active_task",
                    "recent_turns",
                    "conversation_summary",
                    "verified_facts",
                    "uncertainties",
                    "relevant_files",
                    "recent_locators",
                    "project_findings",
                    "build_cells",
                    "integration_state",
                    "wiring_evidence",
                    "neuron_field",
                ),
            ),
            "agent_observatory": self._compact_observatory(source.get("agent_observatory")),
            "steps": self._compact_steps(source.get("steps")),
        }
        compact = self._truncate_strings(compact, 6000)
        text = self._serialize(compact)
        if len(text) <= self.max_state_chars:
            return text

        compact["steps"] = list(compact.get("steps") or [])[-16:]
        compact["agent_observatory"] = self._compact_observatory(
            compact.get("agent_observatory"),
            item_limit=8,
        )
        compact = self._truncate_strings(compact, 1800)
        text = self._serialize(compact)
        if len(text) <= self.max_state_chars:
            return text

        minimal = {
            "context_compacted": True,
            "context_budget_exceeded": True,
            "run": compact.get("run"),
            "workspace": compact.get("workspace"),
            "engineering": compact.get("engineering"),
            "steps": list(compact.get("steps") or [])[-4:],
        }
        return self._serialize(self._truncate_strings(minimal, 400))

    @staticmethod
    def _serialize(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    @staticmethod
    def _select_mapping(value: Any, keys: tuple[str, ...]) -> Any:
        if not isinstance(value, dict):
            return value
        return {key: value.get(key) for key in keys if key in value}

    @staticmethod
    def _compact_steps(value: Any) -> list[Any]:
        steps = list(value or []) if isinstance(value, list) else []
        pinned_kinds = {"run_created", "project_context", "user_input", "completion_review"}
        pinned = [step for step in steps if isinstance(step, dict) and step.get("kind") in pinned_kinds]
        recent = steps[-40:]
        by_sequence: dict[Any, Any] = {}
        for step in (*pinned[-12:], *recent):
            if isinstance(step, dict):
                by_sequence[step.get("sequence", id(step))] = step
        return list(by_sequence.values())

    @staticmethod
    def _compact_observatory(value: Any, item_limit: int = 20) -> Any:
        if not isinstance(value, dict):
            return value
        compact = {key: value.get(key) for key in ("total", "active", "queued", "completed")}
        items = value.get("items") if isinstance(value.get("items"), list) else []
        compact["items"] = items[-item_limit:]
        return compact

    @classmethod
    def _truncate_strings(cls, value: Any, limit: int) -> Any:
        if isinstance(value, str):
            if len(value) <= limit:
                return value
            head = max(200, limit * 2 // 3)
            tail = max(100, limit - head)
            return value[:head] + "\n...[section compacted]...\n" + value[-tail:]
        if isinstance(value, dict):
            return {str(key): cls._truncate_strings(item, limit) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._truncate_strings(item, limit) for item in value]
        return value
