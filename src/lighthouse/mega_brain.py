from __future__ import annotations

from .neuron_brain import NeuronAwareLightHouseBrain
from .provider import AgentDecision


class MegaProjectLightHouseBrain(NeuronAwareLightHouseBrain):
    """Main AI that may freely compose tools and elastic Agents for large work."""

    def _dispatch_tool(self, run, decision, step_number: int):
        capability_name = str(decision.capability or "")
        arguments = dict(decision.arguments or {})
        if capability_name.startswith(("project.", "tools.", "agent.bus.")):
            arguments.setdefault("actor", run.actor)
            if capability_name.startswith("agent.bus."):
                arguments.setdefault("parent_run_id", run.id)
            if capability_name in {"project.create.v1", "tools.recommend.v1"}:
                arguments.setdefault("director_run_id", run.id)
                memory = getattr(self, "memory", None)
                if memory is not None:
                    conversation = memory.conversation_for_run(run.id)
                    if conversation:
                        arguments.setdefault("conversation_id", conversation["id"])
            decision = AgentDecision(
                kind=decision.kind,
                reason=decision.reason,
                capability=decision.capability,
                arguments=arguments,
                message=decision.message,
            )
        return super()._dispatch_tool(run, decision, step_number)

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "The durable Tool Knowledge Registry is available through tools.search.v1, "
            "tools.inspect.v1 and tools.recommend.v1, so you do not need to remember every "
            "tool. Tool recommendations are advisory evidence, never mandatory routing. "
            "For work that you judge to be large, uncertain, cross-module or parallelizable, "
            "you may optionally create a Mega Project knowledge space, dispatch any number "
            "of logical Work Orders through agent.bus.dispatch_many.v1, store distilled "
            "findings with evidence, create or revise project steps, execute directly, "
            "continue investigating, design tests, run regression, or abandon the project "
            "container. There is no fixed investigation-plan-execution workflow and no file "
            "count threshold. You remain Project Director and decide when knowledge is "
            "sufficient, which Agents add value, whether to act directly, and what evidence "
            "is required before completion. Logical Agent population may grow without a "
            "product limit; the durable queue controls physical concurrency. Preserve raw "
            "evidence and distinguish verified facts, inferences, risks, unknowns, conflicts "
            "and recommendations. "
            + base
        )
