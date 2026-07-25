from __future__ import annotations

from lighthouse.mega_context import MegaProjectContextCompiler
from lighthouse.neuron_context import NeuronAwareContextCompiler


class FakeTools:
    def recommend(self, query, **kwargs):
        return {
            "query": query,
            "recommendations": [{"tool_name": "project.create.v1", "advisory_only": True}],
            "scale_advice": {"recommendation": "consider_mega_project_mode", "advisory_only": True},
            "tool_search_available": True,
        }

    def categories(self):
        return [{"category": "mega-project", "tool_count": 8}]


class FakeProjects:
    def active_project(self, **kwargs):
        return {
            "id": "project-1",
            "title": "Large change",
            "goal": "Investigate and execute flexibly",
            "status": "active",
        }

    def inspect_project(self, project_id):
        return {
            "findings": [{"finding_type": "verified_fact", "claim": "fact"}],
            "steps": [{"title": "optional step", "status": "proposed"}],
            "decisions": [],
            "latest_checkpoint": None,
        }


def test_tool_and_project_context_remain_advisory(monkeypatch):
    monkeypatch.setattr(
        NeuronAwareContextCompiler,
        "compile",
        lambda self, **kwargs: {
            "available": True,
            "current_request": {"content": kwargs["query"]},
            "snapshot": {},
        },
    )
    compiler = MegaProjectContextCompiler(
        object(),
        object(),
        object(),
        FakeTools(),
        FakeProjects(),
    )
    bundle = compiler.compile(
        workspace_id="workspace-1",
        actor="operator",
        conversation_id="conversation-1",
        run_id="run-1",
        query="upgrade this large repository",
    )
    assert bundle["tool_context"]["recommendations"][0]["advisory_only"] is True
    assert bundle["active_project"]["id"] == "project-1"
    assert bundle["project_director_brief"]["fixed_workflow"] is False
    assert bundle["project_director_brief"]["main_ai_may_investigate_plan_execute_or_revise_freely"] is True
