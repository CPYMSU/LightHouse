from __future__ import annotations

from typing import Any

from .context_intelligence import ContextCompiler


class NeuronAwareContextCompiler(ContextCompiler):
    """Compile durable context under programmatic control from the persistent neuron field."""

    def __init__(self, memory, agent_bus, neuron_runtime):
        super().__init__(memory, agent_bus)
        self.neuron_runtime = neuron_runtime

    def compile(self, **kwargs: Any) -> dict[str, Any]:
        workspace_id = str(kwargs.get("workspace_id") or "")
        applied = {
            "turn_limit": int(kwargs.get("turn_limit") or 8),
            "file_limit": int(kwargs.get("file_limit") or 16),
        }
        try:
            neural_context = self.neuron_runtime.current_summary(
                workspace_id=workspace_id
            )
            control = (
                neural_context.get("cognitive_control")
                if isinstance(neural_context, dict)
                else {}
            ) or {}
            memory_depth = max(
                0.0, min(float(control.get("memory_depth", 0.5)), 1.0)
            )
            search_depth = max(
                0.0, min(float(control.get("search_depth", 0.5)), 1.0)
            )
            applied["turn_limit"] = max(4, min(16, round(4 + 12 * memory_depth)))
            applied["file_limit"] = max(8, min(40, round(8 + 32 * search_depth)))
            kwargs["turn_limit"] = applied["turn_limit"]
            kwargs["file_limit"] = applied["file_limit"]
            neural_context["available"] = True
            neural_context["freshness"] = "latest_completed_background_snapshot"
        except Exception as exc:
            neural_context = {
                "available": False,
                "workspace_id": workspace_id,
                "error": str(exc),
                "dominant_neurons": [],
                "latest_abm_run": None,
                "cognitive_control": {},
                "freshness": "unavailable",
            }

        bundle = super().compile(**kwargs)
        neural_context["control_applied"] = {
            **applied,
            "mechanism": "programmatic_context_budget",
            "prompt_persona": False,
        }
        bundle["neuron_field"] = neural_context
        bundle["snapshot"] = {
            **(bundle.get("snapshot") or {}),
            "neuron_source": "persistent_background_snapshot",
            "neuron_control_applied": True,
        }
        return bundle
