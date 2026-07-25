from __future__ import annotations

from typing import Any

from .context_intelligence import ContextCompiler


class NeuronAwareContextCompiler(ContextCompiler):
    """Compile durable context together with the latest completed neuron field."""

    def __init__(self, memory, agent_bus, neuron_runtime):
        super().__init__(memory, agent_bus)
        self.neuron_runtime = neuron_runtime

    def compile(self, **kwargs: Any) -> dict[str, Any]:
        workspace_id = str(kwargs.get("workspace_id") or "")
        bundle = super().compile(**kwargs)
        try:
            neural_context = self.neuron_runtime.current_summary(
                workspace_id=workspace_id
            )
            neural_context["available"] = True
            neural_context["freshness"] = "latest_completed_background_snapshot"
        except Exception as exc:
            neural_context = {
                "available": False,
                "workspace_id": workspace_id,
                "error": str(exc),
                "dominant_neurons": [],
                "latest_abm_run": None,
                "freshness": "unavailable",
            }
        bundle["neuron_field"] = neural_context
        bundle["snapshot"] = {
            **(bundle.get("snapshot") or {}),
            "neuron_source": "background_snapshot",
        }
        return bundle
