from __future__ import annotations

from lighthouse.brain import LightHouseBrain
from lighthouse.context_intelligence import ContextCompiler
from lighthouse.neuron_brain import NeuronAwareLightHouseBrain
from lighthouse.neuron_context import NeuronAwareContextCompiler


class FakeNeuronRuntime:
    def __init__(self):
        self.processed = 0
        self.workspace_id = None

    def process_pending(self, *, limit: int):
        self.processed += limit
        return []

    def current_summary(self, *, workspace_id: str):
        self.workspace_id = workspace_id
        return {
            "workspace_id": workspace_id,
            "dominant_neurons": [{"neuron_id": 2, "role": "exploration"}],
            "latest_abm_run": {"global_emotion": {"curiosity": 0.8}},
        }


def test_live_neuron_field_is_injected_after_simple_reflexes(monkeypatch):
    monkeypatch.setattr(
        ContextCompiler,
        "compile",
        lambda self, **kwargs: {
            "available": True,
            "current_request": {"content": kwargs["query"]},
            "snapshot": {"cache": "hit"},
        },
    )
    runtime = FakeNeuronRuntime()
    compiler = NeuronAwareContextCompiler(object(), object(), runtime)

    bundle = compiler.compile(
        workspace_id="workspace-1",
        actor="operator",
        conversation_id=None,
        run_id=None,
        query="continue",
    )

    assert runtime.processed == 4
    assert runtime.workspace_id == "workspace-1"
    assert bundle["neuron_field"]["available"] is True
    assert bundle["neuron_field"]["dominant_neurons"][0]["neuron_id"] == 2
    assert bundle["snapshot"]["neuron_source"] == "live"


def test_main_ai_prompt_treats_neuron_field_as_reflex_evidence(monkeypatch):
    monkeypatch.setattr(
        LightHouseBrain,
        "_system_prompt",
        lambda self, run: "BASE PROMPT",
    )
    brain = object.__new__(NeuronAwareLightHouseBrain)

    prompt = brain._system_prompt(None)

    assert "deterministic reflex evidence" in prompt
    assert "not decorative emotion text" in prompt
    assert "not an automatic refusal boundary" in prompt
    assert prompt.endswith("BASE PROMPT")
