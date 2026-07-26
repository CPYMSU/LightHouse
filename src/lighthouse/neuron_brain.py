from __future__ import annotations

from typing import Any

from .brain import LightHouseBrain


class NeuronAwareLightHouseBrain(LightHouseBrain):
    """Main AI whose retrieval and decision state are controlled by the live neuron field."""

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        context = (
            state.get("context_intelligence")
            if isinstance(state.get("context_intelligence"), dict)
            else {}
        )
        neural = (
            context.get("neuron_field")
            if isinstance(context.get("neuron_field"), dict)
            else {}
        )
        control = (
            neural.get("cognitive_control")
            if isinstance(neural.get("cognitive_control"), dict)
            else {}
        )
        state["cognitive_control"] = control
        state["neuron_runtime_policy"] = {
            "persistent": bool(neural.get("persistent")),
            "cross_session_learning": bool(neural.get("cross_session_learning")),
            "prompt_persona": False,
            "programmatic_controls": [
                "context_budget",
                "memory_depth",
                "tool_candidate_count",
                "verification_depth",
                "planning_depth",
                "execution_bias",
                "novelty_bias",
            ],
        }
        return state

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "Context Intelligence may include a persistent neuron_field generated before model "
            "reasoning. Its cognitive_control has already been applied programmatically to context "
            "and tool budgets; it is runtime state, not a role-play persona instruction. Read the "
            "remaining activations, predictions, attractors and circuits as evidence of the system's "
            "learned attention and decision history. Factual claims still require verified data and "
            "Receipts, and neuron conflict calls for investigation rather than automatic refusal. "
            + base
        )
