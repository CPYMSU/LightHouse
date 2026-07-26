from __future__ import annotations

from typing import Any
from uuid import uuid4

from .addressing import ExecutionAddressResolver
from .agent import AgentRuntime
from .memory_bridge import MemoryRuntimeBridge
from .memory_resolution import (
    compact_memory_context,
    memory_resolution_policy,
    normalize_memory_depth,
)
from .models import (
    ConfirmationMode,
    KernelMode,
    OperationRequest,
    OperationStatus,
    digest_json,
)


class LightHouseBrain(AgentRuntime):
    """Main AI with durable Context Intelligence and freely chosen Agent collaboration."""

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
        self.memory_bridge = MemoryRuntimeBridge(memory, kernel) if memory is not None else None
        self.address_resolver = (
            ExecutionAddressResolver(memory, kernel.repository, kernel.target_resolver)
            if memory is not None
            else None
        )
        self.background_worker = None
        self.usage_store = None
        self.mega_projects = None
        self.massive_build = None

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

    def authorize_auto(self, run_id: str, *, actor: str) -> dict[str, Any]:
        snapshot = super().authorize_auto(run_id, actor=actor)
        if self.memory_bridge is not None:
            self.memory_bridge.sync(run_id, snapshot)
        return snapshot

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
        conversation = None
        if self.memory is not None:
            try:
                conversation = self.memory.conversation_for_run(run_id)
                snapshot["conversation"] = conversation
            except Exception:
                snapshot["conversation"] = None
        run = self.repository.get_agent_run(run_id)
        work_orders: list[dict[str, Any]] = []
        if self.agent_bus is not None:
            try:
                work_orders = self.agent_bus.list_work_orders(
                    workspace_id=run.workspace_id,
                    parent_run_id=run_id,
                    limit=100,
                )
            except Exception:
                work_orders = []
        snapshot["work_orders"] = work_orders
        terminal = {"succeeded", "failed", "cancelled", "superseded"}
        active = {"leased", "running", "waiting_dependency", "waiting_confirmation"}
        snapshot["agent_observatory"] = {
            "total": len(work_orders),
            "active": sum(1 for item in work_orders if item.get("status") in active),
            "queued": sum(1 for item in work_orders if item.get("status") == "queued"),
            "completed": sum(1 for item in work_orders if item.get("status") in terminal),
            "items": work_orders,
        }
        if self.agent_bus is not None and hasattr(self.agent_bus, "coordination_advice"):
            try:
                snapshot["coordination_advice"] = self.agent_bus.coordination_advice(
                    workspace_id=run.workspace_id,
                    parent_run_id=run_id,
                )
            except Exception:
                snapshot["coordination_advice"] = {
                    "recommended_strategy": "main_ai_decides",
                    "advisory_only": True,
                }
        if self.usage_store is not None:
            try:
                snapshot["token_usage"] = self.usage_store.run_and_conversation_summary(
                    run_id=run_id,
                    conversation_id=(conversation or {}).get("id") if conversation else None,
                )
            except Exception:
                snapshot["token_usage"] = {"turn": {"total_tokens": 0}, "conversation": {"total_tokens": 0}}
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
                {"step": step_number, "capability": decision.capability, "error": str(exc)},
            )
            return None
        if run.mode not in {KernelMode.AUTO, capability.kernel}:
            self.repository.append_agent_step(
                run.id,
                "tool_rejected",
                {
                    "step": step_number,
                    "capability": capability.tool_name,
                    "error": f"run mode {run.mode.value} cannot call {capability.kernel.value}",
                },
            )
            return None
        try:
            grounded_arguments = dict(decision.arguments)
            if capability.executor == "agent_bus":
                grounded_arguments.setdefault("parent_run_id", run.id)
                grounded_arguments.setdefault("actor", run.actor)
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
                        "Use Context Intelligence facts or dispatch a Reality Agent, then "
                        "submit a real address. The validator will not silently replace it."
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
            + digest_json({"capability": capability.tool_name, "arguments": grounded_arguments})
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
                operation["operation"]["status"] == OperationStatus.AWAITING_CONFIRMATION.value
                and capability.confirmation == ConfirmationMode.EXPLICIT
                and self._auto_scope_allows(run, operation)
            ):
                self.repository.append_agent_step(
                    run.id,
                    "auto_confirmation",
                    {
                        "step": step_number,
                        "operation_id": operation["operation"]["id"],
                        "actor": run.actor,
                        "scope": run.auto_scope,
                    },
                )
                operation = self.kernel.confirm(operation["operation"]["id"], actor=run.actor)
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
                query = (run.task + " " + recent_input).strip()
                state["usage_context"] = {
                    "workspace_id": workspace.id,
                    "run_id": run.id,
                    "conversation_id": conversation["id"] if conversation else None,
                }
                if self.context_compiler is not None:
                    memory_depth = self._memory_context_depth(run_id)
                    bundle = self.context_compiler.compile(
                        workspace_id=workspace.id,
                        actor=run.actor,
                        conversation_id=conversation["id"] if conversation else None,
                        run_id=run.id,
                        query=query,
                        turn_limit=8,
                        file_limit=16,
                        memory_depth=memory_depth,
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
                        "memory_index": bundle.get("memory_index"),
                    }
                else:
                    memory_depth = self._memory_context_depth(run_id)
                    policy = memory_resolution_policy(memory_depth)
                    raw_memory = self.memory.context(
                        workspace_id=workspace.id,
                        actor=run.actor,
                        conversation_id=conversation["id"] if conversation else None,
                        query=query,
                        message_limit=max(2, policy.turn_limit * 2),
                        file_limit=policy.file_limit,
                    )
                    state["memory"] = compact_memory_context(raw_memory, depth=memory_depth)
            except Exception as exc:
                state["context_intelligence"] = {
                    "available": False,
                    "error": str(exc),
                    "error_type": type(exc).__name__,
                }
        snapshot = self.snapshot(run_id)
        state["agent_observatory"] = snapshot.get("agent_observatory")
        state["coordination_advice"] = snapshot.get("coordination_advice")
        return state

    def _memory_context_depth(self, run_id: str) -> str:
        """Resolve the latest model-requested expansion for this one run.

        New runs always begin at the index tier. The decision ledger is the
        durable authority for a focused or deep recall request, so retries and
        later `advance` calls do not silently widen context again.
        """

        for step in reversed(self.repository.list_agent_steps(run_id)):
            if step.get("kind") != "memory_context_expanded":
                continue
            payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
            try:
                return normalize_memory_depth(payload.get("depth"), default="index")
            except ValueError:
                continue
        return "index"

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "You are the main LightHouse AI operating-system brain and Project Director. "
            "You are trusted to interpret intent, choose subjects, act directly, delegate, "
            "combine both paths, create Build Cells, request investigation, take over work, "
            "or ask the user only when evidence is genuinely insufficient. You decide whether "
            "to wait for all Agents, wait only for critical Agents, continue without waiting, "
            "or work in parallel and review distilled results later. Agent Bus waiting advice is "
            "recommended evidence, never a command. For broad user-facing design, consider "
            "Research and Taste Agents; for implementation consider Frontend and Backend Agents; "
            "before claiming a real full-stack feature consider Wiring Verification. For massive "
            "projects, scalable output should emerge from independent Build Cells, versioned "
            "contracts, isolated Worktrees, non-overlapping write leases, reviewable batches, "
            "incremental integrations and continuous regression—not one unreviewable generation. "
            "Every decision receives Context Intelligence, available tools, Agent status and project "
            "knowledge. Use evidence semantically; do not rely on keyword rules. You may ignore any "
            "recommendation and remain direct when that is better. Files, policies, tests and Receipts "
            "are reality evidence. Never call mock or static UI live unless the complete wiring path is "
            "verified. Never claim completion without suitable Receipt and regression evidence. "
            + base
        )


ReasoningLoop = LightHouseBrain
