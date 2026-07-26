from __future__ import annotations

from dataclasses import replace

from .agent_coordination import build_shared_cognitive_brief
from .agent_results import AgentResultFusionMixin
from .cognitive import CognitiveContinuityMixin
from .engineering import AdaptiveEngineeringMixin
from .neuron_brain import NeuronAwareLightHouseBrain
from .work_intensity import WorkIntensityMixin


class MegaProjectLightHouseBrain(
    WorkIntensityMixin,
    AgentResultFusionMixin,
    CognitiveContinuityMixin,
    AdaptiveEngineeringMixin,
    NeuronAwareLightHouseBrain,
):
    """Main AI that freely composes direct work and Agent Bus 2.0 collaboration."""

    def _dispatch_tool(self, run, decision, step_number: int):
        capability_name = str(decision.capability or "")
        arguments = dict(decision.arguments or {})
        if capability_name.startswith(("project.", "tools.", "agent.bus.")):
            arguments.setdefault("actor", run.actor)
            if capability_name.startswith("agent.bus."):
                arguments.setdefault("parent_run_id", run.id)
                snapshot = self.snapshot(run.id)
                observer = snapshot.get("cognitive_observer")
                cognitive_state = (
                    observer.get("state")
                    if isinstance(observer, dict) and isinstance(observer.get("state"), dict)
                    else {}
                )
                intensity = (
                    snapshot.get("work_intensity")
                    if isinstance(snapshot.get("work_intensity"), dict)
                    else {"selected": "balanced"}
                )
                findings = []
                agent_bus = getattr(self, "agent_bus", None)
                if agent_bus is not None and hasattr(agent_bus, "shared_findings"):
                    try:
                        findings = agent_bus.shared_findings(
                            workspace_id=run.workspace_id,
                            parent_run_id=run.id,
                            limit=30,
                        )
                    except Exception:
                        findings = []
                brief = build_shared_cognitive_brief(
                    cognitive_state=cognitive_state,
                    intensity=intensity,
                    findings=findings,
                )
                if capability_name == "agent.bus.dispatch_many.v1":
                    shared = (
                        dict(arguments.get("shared_payload") or {})
                        if isinstance(arguments.get("shared_payload"), dict)
                        else {}
                    )
                    shared.setdefault("shared_cognitive_brief", brief)
                    shared.setdefault("intensity", intensity)
                    shared.setdefault("collaboration_depth", 0)
                    arguments["shared_payload"] = shared
                else:
                    payload = (
                        dict(arguments.get("payload") or {})
                        if isinstance(arguments.get("payload"), dict)
                        else {}
                    )
                    payload.setdefault("shared_cognitive_brief", brief)
                    payload.setdefault("intensity", intensity)
                    payload.setdefault("collaboration_depth", 0)
                    arguments["payload"] = payload
            if capability_name in {"project.create.v1", "tools.recommend.v1"}:
                arguments.setdefault("director_run_id", run.id)
                memory = getattr(self, "memory", None)
                if memory is not None:
                    conversation = memory.conversation_for_run(run.id)
                    if conversation:
                        arguments.setdefault("conversation_id", conversation["id"])
            decision = replace(decision, arguments=arguments)
        return super()._dispatch_tool(run, decision, step_number)

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "Agent Bus 2.0 is a shared-cognition engineering team, not a collection of isolated chat Agents. "
            "When delegation helps, provide a structured assignment with intent, scope paths or symbols, "
            "deliverables, behavior to preserve and relevant constraints. Prefer existing professional roles: "
            "research, architecture, frontend, backend, data, security, taste, contract, test-design, "
            "wiring-verification, reality, integration and release. Similar active Work Orders are deduplicated; "
            "verified findings are shared; overlapping write intent and contract disagreements are evidence for "
            "your decision, not automatic deadlocks. Agents may request bounded specialist collaboration, but you "
            "remain the only Project Director. The durable Tool Knowledge Registry is available through "
            "tools.search.v1, tools.inspect.v1 and tools.recommend.v1, so you do not need to remember every tool. "
            "Tool recommendations are advisory evidence, never mandatory routing. For genuinely large or "
            "parallelizable work you may create a Mega Project, Build Cells, dependencies, Worktrees and Write "
            "Leases. There is no fixed investigation-plan-execution workflow or file-count threshold. Preserve raw "
            "evidence and distinguish verified facts, assumptions, risks, conflicts and recommendations. "
            + base
        )
