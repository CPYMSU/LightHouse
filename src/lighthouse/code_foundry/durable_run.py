"""Persist CodeFoundry lifecycle events through LightHouse's existing run store."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4

from ..models import AgentRunStatus, KernelMode
from .brief import CodeBrief
from .events import CodeRunEvent, CodeRunEventSink
from .loop import CodeFoundryLoop, CodeRunOutcome
from .models import CodeResultStatus


class AgentStoreCodeRunSink:
    """Append namespaced CodeFoundry events without changing the generic run schema."""

    def __init__(self, repository: Any, run_id: str):
        self.repository = repository
        self.run_id = run_id

    def emit(self, event: CodeRunEvent) -> dict[str, Any]:
        return _append_step(
            self.repository,
            self.run_id,
            f"code_foundry.{event.kind}",
            dict(event.payload),
        )


@dataclass(frozen=True)
class DurableCodeRunOutcome:
    run: dict[str, Any]
    outcome: CodeRunOutcome


class CodeFoundryRunService:
    """Create a durable run, execute one coding loop, and project its terminal state."""

    def __init__(
        self,
        repository: Any,
        *,
        loop_factory: Callable[[CodeRunEventSink], CodeFoundryLoop],
    ):
        self.repository = repository
        self.loop_factory = loop_factory

    async def start_and_run(
        self,
        *,
        brief: CodeBrief,
        workspace_id: str,
        actor: str,
        mode: KernelMode = KernelMode.AUTO,
        max_turns: int = 32,
        run_id: str | None = None,
    ) -> DurableCodeRunOutcome:
        turns = max(1, min(int(max_turns), 64))
        created = _create_run(
            self.repository,
            run_id=run_id or str(uuid4()),
            task=brief.task,
            workspace_id=workspace_id,
            actor=actor,
            mode=mode,
            max_steps=turns,
            auto_confirm=False,
        )
        sink = AgentStoreCodeRunSink(self.repository, created.id)
        sink.emit(CodeRunEvent("run_created", {"brief": brief.public_dict(), "max_turns": turns}))
        _update_run(
            self.repository,
            created.id,
            status=AgentRunStatus.RUNNING,
            response_status="code_foundry_running",
            goal_status="in_progress",
        )
        loop = self.loop_factory(sink)
        outcome = await loop.run(brief)
        status, response_status, goal_status, warning = _terminal_projection(outcome)
        updated = _update_run(
            self.repository,
            created.id,
            status=status,
            current_step=outcome.turns,
            final_message=outcome.result.summary,
            execution_status="succeeded" if outcome.result.status is CodeResultStatus.VERIFIED else "not_verified",
            response_status=response_status,
            goal_status=goal_status,
            warning=warning,
        )
        return DurableCodeRunOutcome(run=updated.public_dict(), outcome=outcome)


def _terminal_projection(outcome: CodeRunOutcome) -> tuple[AgentRunStatus, str, str, str | None]:
    status = outcome.result.status
    if status is CodeResultStatus.VERIFIED:
        return AgentRunStatus.SUCCEEDED, "verified", "completed", None
    if status is CodeResultStatus.NEEDS_INPUT:
        return AgentRunStatus.WAITING_INPUT, "needs_input", "waiting_input", None
    if status is CodeResultStatus.FAILED:
        return AgentRunStatus.FAILED, "verification_failed", "blocked", outcome.result.summary
    return AgentRunStatus.PARTIALLY_COMPLETED, "unverified", "incomplete", outcome.result.summary


def _create_run(repository: Any, **kwargs: Any):
    method = getattr(repository, "create_agent_run", None) or repository.create_run
    return method(**kwargs)


def _update_run(repository: Any, run_id: str, **kwargs: Any):
    method = getattr(repository, "update_agent_run", None) or repository.update_run
    return method(run_id, **kwargs)


def _append_step(repository: Any, run_id: str, kind: str, payload: dict[str, Any]):
    method = getattr(repository, "append_agent_step", None) or repository.append_step
    return method(run_id, kind, payload)
