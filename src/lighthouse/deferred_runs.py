from __future__ import annotations

from threading import Lock, Thread
from typing import Any
from uuid import uuid4

from .models import AgentRunStatus, KernelMode
from .work_intensity import resolve_intensity


class DeferredRunScheduler:
    """Launch main-AI reasoning without holding the HTTP request open."""

    def __init__(self, runtime):
        self.runtime = runtime
        self._lock = Lock()
        self._active: set[str] = set()

    def start(
        self,
        *,
        task: str,
        workspace_id: str,
        actor: str,
        mode: KernelMode = KernelMode.AUTO,
        max_steps: int | None = None,
        auto_confirm: bool = False,
        conversation_id: str | None = None,
        new_conversation: bool = False,
        work_intensity: str = "balanced",
    ) -> dict[str, Any]:
        memory = getattr(self.runtime, "memory", None)
        if memory is None:
            raise RuntimeError("deferred start requires the durable Memory Fabric")
        task = str(task or "").strip()
        actor = str(actor or "").strip()
        if not task:
            raise ValueError("agent task is required")
        if not actor:
            raise ValueError("agent actor is required")
        policy = resolve_intensity(work_intensity)
        requested = policy.initial_main_steps if max_steps is None else int(max_steps)
        seed_steps = min(64, max(1, requested, min(policy.initial_main_steps, 64)))
        run_id = str(uuid4())
        conversation = memory.ensure_conversation(
            workspace_id=workspace_id,
            actor=actor,
            conversation_id=conversation_id,
            new=bool(new_conversation),
            title=task[:120],
        )
        run = self.runtime.repository.create_agent_run(
            run_id=run_id,
            task=task,
            workspace_id=workspace_id,
            actor=actor,
            mode=mode,
            max_steps=seed_steps,
            auto_confirm=bool(auto_confirm),
        )
        memory.link_run(run.id, conversation["id"])
        self.runtime.repository.append_agent_step(
            run.id,
            "run_created",
            {
                "task": task,
                "workspace_id": workspace_id,
                "actor": actor,
                "mode": mode.value,
                "max_steps": seed_steps,
                "effective_initial_steps": policy.initial_main_steps,
                "auto_confirm": bool(auto_confirm),
                "conversation_id": conversation["id"],
                "deferred": True,
                "work_intensity": policy.name,
                "intensity_policy": policy.public_dict(),
            },
        )
        memory.record_message(
            conversation_id=conversation["id"],
            role="user",
            content=task,
            run_id=run.id,
            metadata={
                "kind": "run_created",
                "deferred": True,
                "work_intensity": policy.name,
            },
        )
        memory.start_task(
            run_id=run.id,
            conversation_id=conversation["id"],
            goal=task,
        )
        self.launch(run.id)
        return self.runtime.snapshot(run.id)

    def advance(self, run_id: str) -> dict[str, Any]:
        self.launch(run_id)
        return self.runtime.snapshot(run_id)

    def provide_input(
        self,
        run_id: str,
        *,
        actor: str,
        message: str,
    ) -> dict[str, Any]:
        run = self.runtime.repository.get_agent_run(run_id)
        if run.actor != actor:
            raise PermissionError("only the agent run actor may provide input")
        message = str(message or "").strip()
        if not message:
            raise ValueError("input message is required")
        if run.status != AgentRunStatus.WAITING_INPUT:
            raise ValueError(
                f"agent run is not waiting for input: {run.status.value}"
            )
        self.runtime.repository.append_agent_step(
            run_id,
            "user_input",
            {"actor": actor, "message": message},
        )
        self.runtime.repository.update_agent_run(
            run_id,
            status=AgentRunStatus.RUNNING,
            final_message=None,
        )
        self.launch(run_id)
        return self.runtime.snapshot(run_id)

    def launch(self, run_id: str) -> bool:
        with self._lock:
            if run_id in self._active:
                return False
            self._active.add(run_id)
        Thread(
            target=self._run,
            args=(run_id,),
            name=f"lighthouse-brain-{run_id[:8]}",
            daemon=True,
        ).start()
        return True

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            return run_id in self._active

    def _run(self, run_id: str) -> None:
        try:
            self.runtime.advance(run_id)
        except Exception as exc:
            try:
                self.runtime.repository.append_agent_step(
                    run_id,
                    "provider_error",
                    {
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "deferred": True,
                    },
                )
                self.runtime.repository.update_agent_run(
                    run_id,
                    status=AgentRunStatus.FAILED,
                    final_message=f"Main AI failed: {exc}",
                )
                bridge = getattr(self.runtime, "memory_bridge", None)
                if bridge is not None:
                    bridge.sync(run_id, self.runtime.snapshot(run_id))
            except Exception:
                pass
        finally:
            with self._lock:
                self._active.discard(run_id)
