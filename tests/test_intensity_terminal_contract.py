from pathlib import Path


def test_terminal_supports_saved_and_one_off_work_intensity():
    terminal = Path("src/lighthouse/terminal_v4.py").read_text(encoding="utf-8")
    assert 'WORK_INTENSITY_KEY = "work_intensity"' in terminal
    assert 'line.startswith("/intensity")' in terminal
    assert 'argv[0] == "intensity"' in terminal
    assert '"--intensity" in argv' in terminal
    assert '"work_intensity": policy.name' in terminal
    assert "Intensity controls work depth" in terminal


def test_api_accepts_intensity_at_start_and_during_a_run():
    api = Path("src/lighthouse/api.py").read_text(encoding="utf-8")
    api_v12 = Path("src/lighthouse/api_v12.py").read_text(encoding="utf-8")
    deferred = Path("src/lighthouse/deferred_runs.py").read_text(encoding="utf-8")

    assert 'Literal["quick", "balanced", "advanced", "extreme"]' in api
    assert "work_intensity=payload.work_intensity" in api
    assert '/v1/agent/runs/{run_id}/intensity' in api_v12
    assert "set_work_intensity" in api_v12
    assert '"work_intensity": policy.name' in deferred
    assert "intensity_policy" in deferred


def test_intensity_does_not_grant_permission_or_change_observation_density():
    intensity = Path("src/lighthouse/work_intensity.py").read_text(encoding="utf-8")
    policy_section = intensity.split("class WorkIntensityMixin", 1)[0]
    assert "auto_confirm" not in policy_section
    assert "observe_mode" not in policy_section
    assert "KernelMode" not in policy_section
