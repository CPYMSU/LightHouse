from __future__ import annotations

from typing import Any
from uuid import uuid4

from .agent import AgentRuntime
from .models import KernelMode
from .memory_bridge import MemoryRuntimeBridge


class LightHouseBrain(AgentRuntime):
    """LightHouse's built-in reasoning loop with durable cross-run memory."""

    def __init__(self, repository, kernel, provider, memory=None):
        super().__init__(repository, kernel, provider)
        self.memory = memory
        self.memory_bridge = MemoryRuntimeBridge(memory, kernel) if memory is not None else None

    def start(
        self,
        *,
        task: str,
        workspace_id: str,
        actor: str,
        mode: KernelMode = KernelMode.AUTO,
        max_steps: int = 12,
        auto_confirm: bool = False,
        run_id: str | None = None,
        conversation_id: str | None = None,
        new_conversation: bool = False,
    ) -> dict[str, Any]:
        if self.memory is None:
            return super().start(
                task=task,
                workspace_id=workspace_id,
                actor=actor,
                mode=mode,
                max_steps=max_steps,
                auto_confirm=auto_confirm,
                run_id=run_id,
            )
        task = str(task or "").strip()
        actor = str(actor or "").strip()
        if not task:
            raise ValueError("agent task is required")
        if not actor:
            raise ValueError("agent actor is required")
        max_steps = int(max_steps)
        if not 1 <= max_steps <= 64:
            raise ValueError("max_steps must be between 1 and 64")

        actual_run_id = run_id or str(uuid4())
        conversation = self.memory.ensure_conversation(
            workspace_id=workspace_id,
            actor=actor,
            conversation_id=conversation_id,
            new=bool(new_conversation),
            title=task[:120],
        )
        run = self.repository.create_agent_run(
            run_id=actual_run_id,
            task=task,
            workspace_id=workspace_id,
            actor=actor,
            mode=mode,
            max_steps=max_steps,
            auto_confirm=bool(auto_confirm),
        )
        self.memory.link_run(run.id, conversation["id"])
        if not self.repository.list_agent_steps(run.id):
            self.repository.append_agent_step(
                run.id,
                "run_created",
                {
                    "task": task,
                    "workspace_id": workspace_id,
                    "actor": actor,
                    "mode": mode.value,
                    "max_steps": max_steps,
                    "auto_confirm": bool(auto_confirm),
                    "conversation_id": conversation["id"],
                },
            )
            self.memory.record_message(
                conversation_id=conversation["id"],
                role="user",
                content=task,
                run_id=run.id,
                metadata={"kind": "run_created"},
            )
            self.memory.start_task(run_id=run.id, conversation_id=conversation["id"], goal=task)
        return self.advance(run.id)

    def provide_input(self, run_id: str, *, actor: str, message: str) -> dict[str, Any]:
        snapshot = super().provide_input(run_id, actor=actor, message=message)
        if self.memory_bridge is not None:
            self.memory_bridge.sync(run_id, snapshot)
        return snapshot

    def advance(self, run_id: str) -> dict[str, Any]:
        snapshot = super().advance(run_id)
        if self.memory_bridge is not None:
            self.memory_bridge.sync(run_id, snapshot)
        return snapshot

    def snapshot(self, run_id: str) -> dict[str, Any]:
        snapshot = super().snapshot(run_id)
        if self.memory is not None:
            try:
                snapshot["conversation"] = self.memory.conversation_for_run(run_id)
            except Exception:
                snapshot["conversation"] = None
        return snapshot

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        run = self.repository.get_agent_run(run_id)
        workspace = self.repository.get_workspace(run.workspace_id)
        workspace_state = state.setdefault("workspace", {})
        workspace_state["desktop_target_id"] = workspace.desktop_target_id
        workspace_state["execution_surfaces"] = {
            "data": bool(workspace.data_target_id),
            "system": bool(workspace.system_target_id),
            "desktop": bool(workspace.desktop_target_id),
        }
        catalog = getattr(self.kernel, "data_catalog", None)
        if catalog is not None:
            try:
                data_worlds = catalog.context(workspace.id, resource_limit=80)
            except Exception as exc:
                data_worlds = {"available": False, "error": str(exc), "error_type": type(exc).__name__}
            state["data_worlds"] = data_worlds
            bindings = data_worlds.get("bindings") if isinstance(data_worlds, dict) else None
            workspace_state["execution_surfaces"]["data"] = bool(bindings) or bool(workspace.data_target_id)

        if self.memory is not None:
            try:
                conversation = self.memory.conversation_for_run(run_id)
                recent_input = ""
                for step in reversed(self.repository.list_agent_steps(run_id)):
                    if step.get("kind") == "user_input":
                        payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
                        recent_input = str(payload.get("message") or "")
                        break
                state["memory"] = self.memory.context(
                    workspace_id=workspace.id,
                    actor=run.actor,
                    conversation_id=conversation["id"] if conversation else None,
                    query=(run.task + " " + recent_input).strip(),
                    message_limit=24,
                    file_limit=24,
                )
            except Exception as exc:
                state["memory"] = {"available": False, "error": str(exc), "error_type": type(exc).__name__}
        return state

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "You are the LightHouse AI operating-system brain. The governed worlds are "
            "Data (PostgreSQL), System (files/code/shell/servers), and Desktop "
            "(macOS applications, browsers, and confined files). A goal may require a "
            "sequence across multiple kernels when the run mode is auto. The state may "
            "contain a durable memory bundle with active_task, recent_tasks, recent_messages, "
            "relevant_files and recent_locators. Resolve references such as 'this', 'that file', "
            "'the page from before', 'continue', and 'make it richer' from the active subject "
            "and successful recent Receipts before asking the user again. Prefer the exact "
            "canonical path from memory over broad file searches. Never claim a remembered "
            "file exists without inspecting it or receiving a successful file Receipt. For "
            "directory creation use system.directory.create.v1 instead of shell mkdir. For "
            "Data work, prefer registered semantic commands first, then cataloged resource "
            "capabilities, and use raw SQL only when typed surfaces cannot express the request. "
            "Before using a new data world, sync its catalog. Never invent resource names, "
            "columns, primary keys or semantic commands. Prefer semantic Desktop capabilities "
            "over shell commands such as open, and never use pixel-coordinate guessing when an "
            "exact capability exists. "
            + base
        )


ReasoningLoop = LightHouseBrain
