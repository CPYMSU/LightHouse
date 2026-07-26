from __future__ import annotations

import json
from types import SimpleNamespace

from lighthouse.cognitive_projection import (
    CognitiveProjectionMixin,
    build_complete_capability_map,
    compile_cognitive_projection,
)


class FakeCapability:
    def __init__(self, tool_name: str, *, index: int = 0):
        self.tool_name = tool_name
        self.index = index

    def public_dict(self):
        return {
            "tool_name": self.tool_name,
            "command": self.tool_name.replace(".", " "),
            "description": "Verbose registry description that must not repeat in every prompt.",
            "kernel": "system",
            "risk": "low",
            "confirmation": "direct",
            "writes": False,
            "aliases": [f"alias-{self.index}", "another repeated alias"],
            "arguments": {
                "path": {
                    "type": "string",
                    "required": True,
                    "description": "long schema text" * 20,
                },
                "limit": {"type": "integer", "required": False, "minimum": 1},
            },
        }


class FakeRegistry:
    def __init__(self, count: int = 140):
        self.capabilities = [
            FakeCapability(f"system.file.tool_{index}.v1", index=index)
            for index in range(count)
        ]
        self.capabilities.append(FakeCapability("tools.inspect.v1", index=count))

    def list(self, *, kernel=None):
        return list(self.capabilities)

    def atlas(self, *, kernel=None):
        return [item.public_dict() for item in self.capabilities]


class FakeRepository:
    def get_agent_run(self, run_id: str):
        return SimpleNamespace(id=run_id, mode="auto")


class FakeBaseBrain:
    def __init__(self, state, registry):
        self.state = state
        self.repository = FakeRepository()
        self.kernel = SimpleNamespace(registry=registry)

    def _model_state(self, run_id: str):
        return self.state

    def _system_prompt(self, run):
        atlas = json.dumps(
            self.kernel.registry.atlas(kernel=run.mode),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "BASE PROMPT Capability atlas: " + atlas


class ProjectionBrain(CognitiveProjectionMixin, FakeBaseBrain):
    pass


def _raw_state(current: str = "你能看到上文吗"):
    huge_patch = "@@\n-old\n+new\n" * 3000
    recent_turns = [
        {
            "user": {"content": "想看看当前的神经元激活状态或偏好映射"},
            "assistant": [
                {"content": "当前神经元场会调整搜索、规划、验证和记忆偏好。"}
            ],
            "system": [],
        }
    ]
    context = {
        "current_request": {"content": "会影响到主AI吗"},
        "recent_turns": recent_turns,
        "conversation_summary": {"summary": "正在讨论持久神经元场。"},
        "active_task": {"goal": "理解神经元场是否影响主AI"},
        "candidate_entities": [
            {"type": "concept", "locator": "24-neuron field"}
        ],
        "verified_facts": [],
        "inferences": [],
        "uncertainties": [],
        "relevant_files": [],
        "recent_locators": [],
        "tool_context": {
            "recommendations": [
                {"tool_name": "tools.inspect.v1", "advisory_only": True}
            ],
            "categories": [{"category": "tool-discovery", "tool_count": 3}],
            "neuron_control": {"candidate_limit": 12},
        },
        "neuron_field": {
            "persistent": True,
            "cross_session_learning": True,
            "cognitive_control": {"memory_depth": 0.7},
        },
    }
    return {
        "run": {
            "id": "run-1",
            "task": "会影响到主AI吗",
            "workspace_id": "workspace-1",
        },
        "workspace": {"id": "workspace-1", "name": "desktop", "config": {}},
        "usage_context": {"workspace_id": "workspace-1", "run_id": "run-1"},
        "steps": [
            {
                "sequence": 1,
                "kind": "run_created",
                "payload": {"task": "会影响到主AI吗"},
            },
            {
                "sequence": 2,
                "kind": "input_required",
                "payload": {"message": "您指的是哪个操作或变更？"},
            },
            {
                "sequence": 3,
                "kind": "user_input",
                "payload": {"message": current},
            },
            {
                "sequence": 4,
                "kind": "decision",
                "payload": {
                    "kind": "tool",
                    "capability": "system.file.patch.v1",
                    "arguments": {"patch": huge_patch},
                    "reason": "large raw payload should not be repeated",
                },
            },
        ],
        "context_intelligence": context,
        "memory": dict(context),
        "cognitive_continuity": {
            "state": {
                "goal": {"summary": "理解神经元场"},
                "user_directions": [current],
            },
            "recent_updates": [],
            "recent_activity": [],
        },
        "agent_observatory": {
            "total": 0,
            "active": 0,
            "queued": 0,
            "completed": 0,
            "items": [],
        },
        "agent_execution_activity": {"recent": []},
        "agent_results": [],
        "data_worlds": {"bindings": [], "count": 0},
        "engineering": {
            "operating_principle": "preserve the active implementation",
            "cognitive_continuity": {"duplicated": True},
        },
        "cognitive_control": {"memory_depth": 0.7},
    }


def test_complete_capability_map_has_every_tool_without_top_k_boundary():
    registry = FakeRegistry(count=140)
    capability_map = build_complete_capability_map(registry, kernel="auto")

    assert capability_map["complete"] is True
    assert capability_map["ranked"] is False
    assert capability_map["semantic_limit"] is None
    assert capability_map["tool_count"] == 141
    assert capability_map["exact_tool_names"][0] == "system.file.tool_0.v1"
    assert capability_map["exact_tool_names"][-1] == "tools.inspect.v1"
    assert len(capability_map["exact_tool_names"]) == len(registry.capabilities)
    first_tool = capability_map["domains"][0]["tools"][0]
    assert first_tool["arguments"] == ["path:string!", "limit:integer"]
    assert capability_map["schema_expansion"]["main_ai_may_expand_any_node"] is True


def test_projection_preserves_dialogue_relationship_and_folds_duplicates():
    raw = _raw_state()
    capability_map = build_complete_capability_map(
        FakeRegistry(count=4), kernel="auto"
    )
    projected = compile_cognitive_projection(raw, capability_map=capability_map)

    assert "steps" not in projected
    assert "memory" not in projected
    assert "context_intelligence" not in projected
    assert projected["dialogue_focus"]["current_user_message"] == "你能看到上文吗"
    assert (
        projected["dialogue_focus"]["preceding_assistant_move"]["content"]
        == "您指的是哪个操作或变更？"
    )
    assert projected["dialogue_focus"]["recent_complete_turns"][0]["assistant"][0][
        "content"
    ].startswith("当前神经元场")
    assert projected["run_ledger"]["event_count"] == 4
    decision_event = projected["run_ledger"]["events"][-1]
    assert decision_event["capability"] == "system.file.patch.v1"
    assert "arguments" not in decision_event
    assert projected["capability_world"]["complete_map"]["tool_count"] == 5
    assert projected["capability_world"]["current_focus"]["neuron_control"][
        "not_a_visibility_boundary"
    ] is True
    assert "cognitive_continuity" not in projected["engineering"]
    assert projected["cognition_receipt"]["keyword_routing"] is False
    assert projected["cognition_receipt"]["semantic_restrictions_added"] is False
    assert (
        projected["cognition_receipt"]["projected_state_chars"]
        < projected["cognition_receipt"]["raw_state_chars"] / 3
    )


def test_projection_has_no_greeting_specific_fast_path_or_world_reduction():
    registry = FakeRegistry(count=12)
    greeting = ProjectionBrain(_raw_state("你好"), registry)._model_state("run-1")
    engineering = ProjectionBrain(
        _raw_state("继续审计整个项目"), registry
    )._model_state("run-1")

    assert greeting["capability_world"]["complete_map"]["exact_tool_names"] == (
        engineering["capability_world"]["complete_map"]["exact_tool_names"]
    )
    assert greeting["cognition_receipt"]["world_coverage"] == engineering[
        "cognition_receipt"
    ]["world_coverage"]
    assert greeting["dialogue_focus"]["current_user_message"] == "你好"
    assert engineering["dialogue_focus"]["current_user_message"] == "继续审计整个项目"


def test_system_prompt_references_single_state_atlas_not_full_schemas():
    registry = FakeRegistry(count=50)
    brain = ProjectionBrain(_raw_state(), registry)
    run = brain.repository.get_agent_run("run-1")

    prompt = brain._system_prompt(run)

    assert "state.capability_world.complete_map" in prompt
    assert "not ranked, filtered or a semantic boundary" in prompt
    assert "another repeated alias" not in prompt
    assert "long schema text" not in prompt
    assert len(prompt) < 1200
