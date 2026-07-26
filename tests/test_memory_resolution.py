from __future__ import annotations

from lighthouse.memory_resolution import compact_memory_context, memory_resolution_policy
from lighthouse.provider import parse_decision


def _memory_source():
    return {
        "active_task": {
            "id": "task-1",
            "goal": "Refactor the main service without breaking the public API.",
            "status": "active",
            "summary": "Previous work isolated the request parser and left one open migration question.",
        },
        "conversation_summary": {
            "summary": "Older discussion established the API compatibility requirement. " * 80,
            "entities": [{"name": f"entity-{index}", "detail": "x" * 300} for index in range(16)],
            "relations": [{"from": f"entity-{index}", "to": "service"} for index in range(16)],
            "uncertainties": [{"question": f"question-{index}", "evidence": ["x" * 300]} for index in range(16)],
            "distillation_level": 2,
        },
        "recent_turns": [
            {
                "user": {"id": index, "content": f"user turn {index}: " + "u" * 900},
                "assistant": [{"id": 100 + index, "content": f"assistant turn {index}: " + "a" * 900}],
            }
            for index in range(10)
        ],
        "recent_tasks": [
            {"id": f"task-{index}", "goal": "g" * 600, "status": "completed"}
            for index in range(16)
        ],
        "relevant_files": [
            {"canonical_path": f"/repo/src/module_{index}.py", "content_hash": f"hash-{index}"}
            for index in range(24)
        ],
        "recent_locators": [
            {"kind": "file", "canonical_value": f"/repo/src/module_{index}.py"}
            for index in range(24)
        ],
    }


def _records(prefix: str, count: int):
    return [
        {"claim": f"{prefix}-{index}", "evidence": ["e" * 500], "detail": "d" * 500}
        for index in range(count)
    ]


def test_memory_resolution_starts_with_a_small_index_and_expands_deliberately():
    source = _memory_source()
    candidates = _records("entity", 24)
    facts = _records("fact", 40)
    inferences = _records("inference", 24)
    uncertainties = _records("uncertainty", 20)

    index = compact_memory_context(
        source,
        depth="index",
        candidates=candidates,
        facts=facts,
        inferences=inferences,
        uncertainties=uncertainties,
    )
    focused = compact_memory_context(
        source,
        depth="focused",
        candidates=candidates,
        facts=facts,
        inferences=inferences,
        uncertainties=uncertainties,
    )
    deep = compact_memory_context(
        source,
        depth="deep",
        candidates=candidates,
        facts=facts,
        inferences=inferences,
        uncertainties=uncertainties,
    )

    assert len(index["recent_turns"]) == 1
    assert len(index["verified_facts"]) == 8
    assert len(index["relevant_files"]) == 5
    assert index["memory_index"]["tier"] == "index"
    assert index["memory_index"]["expansion"]["request"] == {
        "kind": "memory_expand",
        "arguments": {"depth": "focused"},
    }
    assert index["memory_index"]["estimated_tokens"] < 4_500
    assert focused["memory_index"]["estimated_tokens"] > index["memory_index"]["estimated_tokens"]
    assert deep["memory_index"]["estimated_tokens"] > focused["memory_index"]["estimated_tokens"]
    assert deep["memory_index"]["expansion"]["available"] is False
    assert memory_resolution_policy("deep").turn_limit == 8


def test_model_can_request_only_the_next_memory_tier_not_an_unbounded_dump():
    decision = parse_decision(
        {
            "kind": "memory_expand",
            "arguments": {"depth": "focused"},
            "reason": "The compact index lacks the earlier migration decision.",
        }
    )

    assert decision.kind == "memory_expand"
    assert decision.arguments == {"depth": "focused"}


def test_compacting_active_task_preserves_unset_subject_fields():
    compact = compact_memory_context(
        {"active_task": {"id": "task-1", "goal": "Continue the task", "subject": None}},
        depth="index",
    )

    assert compact["active_task"]["id"] == "task-1"
    assert compact["active_task"]["subject"] is None
    assert compact["active_task"]["subject_display"] is None
