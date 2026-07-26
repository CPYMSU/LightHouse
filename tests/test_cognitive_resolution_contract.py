from __future__ import annotations

from lighthouse.context_intelligence import ContextCompiler
from lighthouse.cognitive_projection import CognitiveProjectionMixin
from lighthouse.mega_brain import MegaProjectLightHouseBrain
from lighthouse.neuron_context import NeuronAwareContextCompiler


class LowDepthNeuronRuntime:
    def current_summary(self, *, workspace_id: str):
        return {
            "workspace_id": workspace_id,
            "cognitive_control": {
                "memory_depth": 0.0,
                "search_depth": 0.0,
            },
        }


def test_neuron_resolution_never_reduces_explicit_context_request(monkeypatch):
    captured = {}

    def fake_compile(self, **kwargs):
        captured.update(kwargs)
        return {"available": True, "snapshot": {}}

    monkeypatch.setattr(ContextCompiler, "compile", fake_compile)
    compiler = NeuronAwareContextCompiler(
        object(), object(), LowDepthNeuronRuntime()
    )

    bundle = compiler.compile(
        workspace_id="workspace-1",
        actor="operator",
        conversation_id="conversation-1",
        run_id="run-1",
        query="continue",
        turn_limit=12,
        file_limit=30,
    )

    assert captured["turn_limit"] == 12
    assert captured["file_limit"] == 30
    applied = bundle["neuron_field"]["control_applied"]
    assert applied["requested"] == {"turn_limit": 12, "file_limit": 30}
    assert applied["recommended"] == {"turn_limit": 4, "file_limit": 8}
    assert applied["not_a_permanent_visibility_boundary"] is True
    assert applied["main_ai_may_expand_context"] is True


def test_canonical_projection_runs_after_all_full_state_mixins():
    assert MegaProjectLightHouseBrain.__mro__[1] is CognitiveProjectionMixin
