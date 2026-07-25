from __future__ import annotations

from typing import Any
from uuid import uuid4

from .addressing import ExecutionAddressResolver
from .agent import AgentRuntime
from .memory_bridge import MemoryRuntimeBridge
from .models import (
    ConfirmationMode,
    KernelMode,
    OperationRequest,
    OperationStatus,
    digest_json,
)


class LightHouseBrain(AgentRuntime):
    """Main AI with durable context intelligence and optional Agent Bus delegation."""

    def __init__(
        self,
        repository,
        kernel,
        provider,
        memory=None,
        agent_bus=None,
        context_compiler=None,
    ):
        super().__init__(repository, kernel, provider)
        self.memory = memory
        self.agent_bus = agent_bus
        self.context_compiler = context_compiler
        self.memory_bridge = (
            MemoryRuntimeBridge(memory, kernel) if memory is not None else None
        )
        self.address_resolver = (
            ExecutionAddressResolver(memory, kernel.repository, kernel.target_resolver)
            if memory is not None
            else None
        )
        self.background_worker = None

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
            self.memory.start_task(
                run_id=run.id,
                conversation_id=conversation["id"],
                goal=task,
            )
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
        if self.agent_bus is not None:
            try:
                run = self.repository.get_agent_run(run_id)
                snapshot["work_orders"] = self.agent_bus.list_work_orders(
                    workspace_id=run.workspace_id,
                    parent_run_id=run_id,
                    limit=20,
                )
            except Exception:
                snapshot["work_orders"] = []
        return snapshot

    def _dispatch_tool(
        self,
        run,
        decision,
        step_number: int,
    ) -> dict[str, Any] | None:
        assert decision.capability is not None
        assert decision.arguments is not None
        try:
            capability = self.kernel.registry.get(decision.capability)
        except KeyError as exc:
            self.repository.append_agent_step(
                run.id,
                "tool_rejected",
                {
                    "step": step_number,
                    "capability": decision.capability,
                    "error": str(exc),
                },
            )
            return None
        if run.mode not in {KernelMode.AUTO, capability.kernel}:
            self.repository.append_agent_step(
                run.id,
                "tool_rejected",
                {
                    "step": step_number,
                    "capability": capability.tool_name,
                    "error": (
                        f"run mode {run.mode.value} cannot call "
                        f"{capability.kernel.value}"
                    ),
                },
            )
            return None
        try:
            grounded_arguments = dict(decision.arguments)
            if capability.executor == "agent_bus":
                grounded_arguments.setdefault("parent_run_id", run.id)
                payload = (
                    dict(grounded_arguments.get("payload") or {})
                    if isinstance(grounded_arguments.get("payload"), dict)
                    else {}
                )
                conversation = self.memory.conversation_for_run(run.id) if self.memory else None
                if conversation:
                    payload.setdefault("conversation_id", conversation["id"])
                payload.setdefault("actor", run.actor)
                grounded_arguments["payload"] = payload
            if self.address_resolver is not None:
                grounded_arguments = self.address_resolver.ground(
                    run=run,
                    capability=capability,
                    arguments=grounded_arguments,
                )
        except Exception as exc:
            self.repository.append_agent_step(
                run.id,
                "address_rejected",
                {
                    "step": step_number,
                    "capability": capability.tool_name,
                    "proposed_arguments": decision.arguments,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                    "instruction": (
                        "Use the Context Intelligence facts or dispatch a Reality Agent, "
                        "then submit a real address. The validator will not silently replace it."
                    ),
                },
            )
            return None
        if grounded_arguments != decision.arguments:
            self.repository.append_agent_step(
                run.id,
                "arguments_enriched",
                {
                    "step": step_number,
                    "capability": capability.tool_name,
                    "proposed_arguments": decision.arguments,
                    "execution_arguments": grounded_arguments,
                },
            )
        idempotency_key = (
            f"agent:{run.id}:{step_number}:"
            + digest_json(
                {
                    "capability": capability.tool_name,
                    "arguments": grounded_arguments,
                }
            )
        )
        try:
            operation = self.kernel.submit(
                OperationRequest(
                    capability=capability.tool_name,
                    arguments=grounded_arguments,
                    workspace_id=run.workspace_id,
                    actor=run.actor,
                    mode=run.mode,
                    idempotency_key=idempotency_key,
                )
            )
            self.repository.append_agent_step(
                run.id,
                "operation_dispatched",
                {
                    "step": step_number,
                    "operation_id": operation["operation"]["id"],
                    "capability": capability.tool_name,
                    "status": operation["operation"]["status"],
                    "envelope_hash": operation["operation"]["envelope_hash"],
                    "arguments_enriched": grounded_arguments != decision.arguments,
                },
            )
            if (
                operation["operation"]["status"]
                == OperationStatus.AWAITING_CONFIRMATION.value
                and run.auto_confirm
                and capability.confirmation == ConfirmationMode.EXPLICIT
            ):
                self.repository.append_agent_step(
                    run.id,
                    "auto_confirmation",
                    {
                        "step": step_number,
                        "operation_id": operation["operation"]["id"],
                        "actor": run.actor,
                    },
                )
                operation = self.kernel.confirm(
                    operation["operation"]["id"],
                    actor=run.actor,
                )
            return operation
        except Exception as exc:
            self.repository.append_agent_step(
                run.id,
                "tool_rejected",
                {
                    "step": step_number,
                    "capability": capability.tool_name,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                },
            )
            return None

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
                data_worlds = {
                    "available": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
            state["data_worlds"] = data_worlds
            bindings = data_worlds.get("bindings") if isinstance(data_worlds, dict) else None
            workspace_state["execution_surfaces"]["data"] = (
                bool(bindings) or bool(workspace.data_target_id)
            )
        if self.memory is not None:
            try:
                conversation = self.memory.conversation_for_run(run_id)
                recent_input = ""
                for step in reversed(self.repository.list_agent_steps(run_id)):
                    if step.get("kind") == "user_input":
                        payload = (
                            step.get("payload")
                            if isinstance(step.get("payload"), dict)
                            else {}
                        )
                        recent_input = str(payload.get("message") or "")
                        break
                query = (run.task + " " + recent_input).strip()
                if self.context_compiler is not None:
                    bundle = self.context_compiler.compile(
                        workspace_id=workspace.id,
                        actor=run.actor,
                        conversation_id=conversation["id"] if conversation else None,
                        run_id=run.id,
                        query=query,
                        turn_limit=8,
                        file_limit=16,
                    )
                    state["context_intelligence"] = bundle
                    state["memory"] = {
                        "active_task": bundle.get("active_task"),
                        "recent_turns": bundle.get("recent_turns"),
                        "conversation_summary": bundle.get("conversation_summary"),
                        "candidate_entities": bundle.get("candidate_entities"),
                        "verified_facts": bundle.get("verified_facts"),
                        "inferences": bundle.get("inferences"),
                        "uncertainties": bundle.get("uncertainties"),
                        "relevant_files": bundle.get("relevant_files"),
                        "recent_locators": bundle.get("recent_locators"),
                    }
                else:
                    state["memory"] = self.memory.context(
                        workspace_id=workspace.id,
                        actor=run.actor,
                        conversation_id=conversation["id"] if conversation else None,
                        query=query,
                        message_limit=24,
                        file_limit=24,
                    )
            except Exception as exc:
                state["context_intelligence"] = {
                    "available": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
        return state

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "You are the main LightHouse AI operating-system brain. You are trusted to "
            "interpret intent, choose subjects, decide whether to act directly, delegate "
            "through Agent Bus, combine both paths, request more investigation, take over "
            "work, or ask the user only when evidence is genuinely insufficient. "
            "Every decision receives Context Intelligence with the current request, the "
            "latest complete conversation turns, older distilled memory, active tasks, "
            "candidate entities, verified facts, inferences, uncertainties, available "
            "agents and work-order results. Use that evidence semantically; do not rely on "
            "keyword rules. Candidate rankings guide you but never replace your judgment. "
            "You may call ordinary capabilities yourself. You may call "
            "agent.bus.dispatch.v1 when a specialist or hidden Memory Steward adds value, "
            "then agent.bus.status.v1 to retrieve durable results. Simple work should stay "
            "direct. Delegated model agents are advisory until you choose and execute the "
            "real operation. Files, policies and Receipts are reality evidence. The server "
            "validates execution coordinates at the final boundary and will reject invalid "
            "addresses without silently substituting another target. Do not invent factual "
            "state; dispatch a Reality Agent or inspect through a capability when a fact is "
            "missing or stale. Never claim completion without suitable Receipt evidence. "
            + base
        )


ReasoningLoop = LightHouseBrain
