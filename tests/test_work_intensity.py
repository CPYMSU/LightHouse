from __future__ import annotations

import pytest

from lighthouse.work_intensity import (
    POLICIES,
    intensity_from_steps,
    normalize_intensity,
    resolve_intensity,
)


def test_work_intensity_has_four_distinct_adaptive_policies():
    assert set(POLICIES) == {"quick", "balanced", "advanced", "extreme"}
    assert POLICIES["quick"].reasoning_effort == "low"
    assert POLICIES["balanced"].reasoning_effort == "medium"
    assert POLICIES["advanced"].reasoning_effort == "high"
    assert POLICIES["extreme"].reasoning_effort == "max"
    assert POLICIES["quick"].hard_main_steps < POLICIES["balanced"].hard_main_steps
    assert POLICIES["balanced"].hard_main_steps < POLICIES["advanced"].hard_main_steps
    assert POLICIES["advanced"].hard_main_steps < POLICIES["extreme"].hard_main_steps
    assert POLICIES["quick"].collaboration_depth == 0
    assert POLICIES["extreme"].collaboration_depth == 3


def test_intensity_aliases_and_invalid_values_are_explicit():
    assert normalize_intensity("快速") == "quick"
    assert normalize_intensity("平衡") == "balanced"
    assert normalize_intensity("高級") == "advanced"
    assert normalize_intensity("極致") == "extreme"
    assert resolve_intensity("pro").name == "extreme"
    with pytest.raises(ValueError, match="quick, balanced, advanced or extreme"):
        normalize_intensity("unlimited")


def test_latest_durable_intensity_event_wins():
    steps = [
        {"sequence": 1, "kind": "run_created", "payload": {"work_intensity": "quick"}},
        {"sequence": 2, "kind": "intensity_changed", "payload": {"from": "quick", "to": "advanced"}},
        {"sequence": 3, "kind": "decision", "payload": {"kind": "tool"}},
        {"sequence": 4, "kind": "intensity_changed", "payload": {"from": "advanced", "to": "extreme"}},
    ]
    assert intensity_from_steps(steps) == "extreme"


def test_intensity_is_resource_preference_not_observe_or_auto_policy():
    for policy in POLICIES.values():
        public = policy.public_dict()
        assert "observe_mode" not in public
        assert "auto_confirm" not in public
        assert "kernel_mode" not in public
        assert public["parallelism_hint"] >= 1
        assert public["verification_depth"]
