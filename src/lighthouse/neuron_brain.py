from __future__ import annotations

from .brain import LightHouseBrain


class NeuronAwareLightHouseBrain(LightHouseBrain):
    """Main AI that interprets the live reflex field as cognitive evidence."""

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "Context Intelligence may include a neuron_field generated before model "
            "reasoning. It is deterministic reflex evidence from twenty-four autonomous "
            "data neurons, not decorative emotion text. Read dominant_neurons, their "
            "activation, valence, confidence and prediction together with the latest ABM "
            "global_emotion. Use it to select attention, memory depth, caution, exploration "
            "and recovery strategy, while still grounding factual claims in verified data "
            "and Receipts. Conflicting neurons are a signal to investigate; they are not an "
            "automatic refusal boundary. "
            + base
        )
