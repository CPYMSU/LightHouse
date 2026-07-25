from __future__ import annotations

from typing import Any

from .context_intelligence import ContextCompiler


class NeuronAwareContextCompiler(ContextCompiler):
    """Compile ordinary durable context together with the live neuron field."""

    def __init__(self, memory, agent_bus, neuron_runtime):
        super().__init__(memory, agent_bus)
        self.neuron_runtime = neuron_runtime

    def compile(self, **kwargs: Any) -> dict[str, Any]:
        workspace_id = str(kwargs.get("workspace_id") or "")
        processing_error: str | None = None
        try:
            # Simple reflexes are deterministic and must settle before the main AI
            # interprets the database change. SKIP LOCKED keeps this multi-instance safe.
            self.neuron_runtime.process_pending(limit=4)
        except Exception as exc:
            processing_error = str(exc)

        bundle = super().compile(**kwargs)
        try:
            neural_context = self.neuron_runtime.current_summary(
                workspace_id=workspace_id
            )
            neural_context["available"] = True
        except Exception as exc:
            neural_context = {
                "available": False,
                "workspace_id": workspace_id,
                "error": str(exc),
                "dominant_neurons": [],
                "latest_abm_run": None,
            }
        if processing_error:
            neural_context["processing_error"] = processing_error
        bundle["neuron_field"] = neural_context
        bundle["snapshot"] = {
            **(bundle.get("snapshot") or {}),
            "neuron_source": "live",
        }
        return bundle
