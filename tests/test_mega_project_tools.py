from __future__ import annotations

from types import SimpleNamespace

from lighthouse.agent_capabilities import AGENT_BUS_CAPABILITIES
from lighthouse.mega_brain import MegaProjectLightHouseBrain
from lighthouse.mega_project_capabilities import MEGA_PROJECT_CAPABILITIES
from lighthouse.neuron_brain import NeuronAwareLightHouseBrain


def test_mega_project_tools_are_composable_primitives():
    tools = {item.tool_name: item for item in MEGA_PROJECT_CAPABILITIES}
    assert "tools.search.v1" in tools
    assert "tools.recommend.v1" in tools
    assert "project.create.v1" in tools
    assert "project.finding.store.v1" in tools
    assert "project.step.create.v1" in tools
    assert "mega_project.execute_everything.v1" not in tools
    assert all(item.confirmation.value == "direct" for item in tools.values())


def test_agent_bus_supports_elastic_logical_population():
    tools = {item.tool_name: item for item in AGENT_BUS_CAPABILITIES}
    batch = tools["agent.bus.dispatch_many.v1"]
    assert batch.arguments["work_orders"]["type"] == "array"
    assert "max_items" not in batch.arguments["work_orders"]
    assert "agent.bus.results.v1" in tools
    assert "agent.bus.findings.v1" in tools
    assert "agent.bus.conflicts.v1" in tools


def test_main_ai_prompt_keeps_mega_project_mode_optional(monkeypatch):
    monkeypatch.setattr(
        NeuronAwareLightHouseBrain,
        "_system_prompt",
        lambda self, run: "BASE PROMPT",
    )
    brain = object.__new__(MegaProjectLightHouseBrain)
    brain.repository = SimpleNamespace(
        list_agent_steps=lambda run_id: [
            {
                "sequence": 1,
                "kind": "run_created",
                "payload": {"work_intensity": "balanced"},
            }
        ]
    )
    prompt = brain._system_prompt(SimpleNamespace(id="run-1"))
    assert "Tool recommendations are advisory" in prompt
    assert "There is no fixed" in prompt
    assert "you remain the only Project Director" in prompt
    assert "BALANCED work intensity" in prompt
    assert prompt.endswith("BASE PROMPT")
