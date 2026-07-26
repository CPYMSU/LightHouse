from __future__ import annotations

from lighthouse.agent_coordination import (
    build_shared_cognitive_brief,
    merge_work_payload,
    prepare_work_order_payload,
)
from lighthouse.agent_results import fuse_agent_results, normalise_agent_result


def test_structured_work_order_is_deterministic_and_existing_code_first():
    raw = {
        "assignment": {
            "intent": "investigate_and_patch",
            "scope": {"paths": ["src/app.py"], "symbols": ["main"]},
            "deliverables": ["root cause", "tests"],
            "preserve": ["public behavior"],
        },
        "intensity": "advanced",
    }
    first = prepare_work_order_payload("backend", "Fix the active implementation", raw)
    second = prepare_work_order_payload("backend", "Fix the active implementation", raw)

    assert first["assignment"]["execution_profile"] == "implementation"
    assert first["assignment"]["constraints"]["existing_code_first"] is True
    assert first["coordination"]["dedupe_key"] == second["coordination"]["dedupe_key"]
    assert first["coordination"]["write_intent"]["paths"] == ["src/app.py"]
    assert first["intensity"]["selected"] == "advanced"
    assert first["local_cognitive_state"]["active_files"] == ["src/app.py"]


def test_payload_merge_keeps_new_context_without_duplicate_lists():
    merged = merge_work_payload(
        {
            "assignment": {"deliverables": ["root cause"], "scope": {"paths": ["a.py"]}},
            "shared_findings": [{"claim": "fact A"}],
        },
        {
            "assignment": {"deliverables": ["root cause", "tests"], "scope": {"paths": ["a.py", "b.py"]}},
            "shared_findings": [{"claim": "fact A"}, {"claim": "fact B"}],
        },
    )
    assert merged["assignment"]["deliverables"] == ["root cause", "tests"]
    assert merged["assignment"]["scope"]["paths"] == ["a.py", "b.py"]
    assert merged["shared_findings"] == [{"claim": "fact A"}, {"claim": "fact B"}]


def test_shared_cognitive_brief_prioritizes_user_direction_and_verified_facts():
    brief = build_shared_cognitive_brief(
        cognitive_state={
            "goal": {"summary": "Upgrade Agent Bus"},
            "user_directions": ["Do not create a parallel scheduler"],
            "strategy": {"current": "reuse durable Work Orders"},
            "verified_facts": [{"claim": "Run Steps are durable"}],
            "open_questions": ["How should conflicts be surfaced?"],
            "active_work": {"changed_files": ["src/lighthouse/agent_bus.py"]},
        },
        intensity={"selected": "advanced"},
        findings=[{"claim": "Wildcard scope was not inherited"}],
    )
    assert brief["user_directions"] == ["Do not create a parallel scheduler"]
    assert brief["verified_facts"][0]["claim"] == "Run Steps are durable"
    assert brief["related_findings"][0]["claim"] == "Wildcard scope was not inherited"
    assert brief["current_diff"]["changed_files"] == ["src/lighthouse/agent_bus.py"]


def test_structured_implementation_result_and_main_ai_fusion():
    work = {
        "id": "work-1",
        "role": "backend",
        "goal": "Patch wildcard inheritance",
        "payload": {"assignment": {"execution_profile": "implementation"}},
        "status": "succeeded",
    }
    agent = {"id": "agent-1", "metadata": {"execution_profile": "implementation"}}
    result = normalise_agent_result(
        agent=agent,
        work_order=work,
        result={
            "summary": "Wildcard inheritance fixed",
            "findings": [
                {
                    "claim": "The worker now accepts the wildcard",
                    "status": "verified",
                    "confidence": 1,
                    "evidence": [{"file": "background_intelligence.py"}],
                }
            ],
            "complete": True,
        },
        tool_results=[
            {
                "ok": True,
                "capability": "system.file.patch.v1",
                "arguments": {"patch": "+++ b/src/lighthouse/background_intelligence.py\n"},
                "operation": {"id": "operation-1"},
                "receipt": {"ok": True, "result_hash": "hash-1"},
            },
            {
                "ok": True,
                "capability": "system.test.run.v1",
                "arguments": {"command": "pytest -q tests/test_agent_bus2.py"},
                "operation": {"id": "operation-2"},
                "receipt": {"ok": True, "result_hash": "hash-2"},
            },
        ],
    )
    assert result["result_type"] == "implementation"
    assert result["changed_files"] == ["src/lighthouse/background_intelligence.py"]
    assert result["tests"][0]["status"] == "passed"
    assert len(result["completion_evidence"]) == 2

    fused = fuse_agent_results(
        {"verified_facts": [], "assumptions": [], "open_questions": [], "completed": []},
        [{**work, "result": result}],
    )
    assert fused["verified_facts"][0]["claim"] == "The worker now accepts the wildcard"
    assert fused["completed"][0]["work_order_id"] == "work-1"
    assert fused["agent_results"][0]["result_type"] == "implementation"
