from pathlib import Path


def test_cognitive_continuity_uses_existing_runtime_and_event_store():
    cognitive = Path("src/lighthouse/cognitive.py").read_text(encoding="utf-8")
    brain = Path("src/lighthouse/mega_brain.py").read_text(encoding="utf-8")
    bootstrap = Path("src/lighthouse/bootstrap.py").read_text(encoding="utf-8")
    results = Path("src/lighthouse/agent_results.py").read_text(encoding="utf-8")
    migrations = Path("src/lighthouse/bootstrap.py").read_text(encoding="utf-8")

    assert "class CognitiveContinuityMixin" in cognitive
    assert "build_cognitive_observer" in cognitive
    assert 'snapshot["cognitive_observer"]' in cognitive
    assert 'state["cognitive_continuity"]' in cognitive
    assert "CognitiveContinuityMixin" in brain
    assert "AgentBusStructuredProvider" in bootstrap
    assert "class AgentBusStructuredProvider(CognitiveStructuredProvider)" in results
    assert '"0008_operation_event_sequence.sql"' in migrations
    assert '"0009_' not in migrations


def test_cognitive_api_and_terminal_controls_are_public_contracts():
    api = Path("src/lighthouse/api_v12.py").read_text(encoding="utf-8")
    terminal = Path("src/lighthouse/terminal_v4.py").read_text(encoding="utf-8")
    ui = Path("src/lighthouse/ui_v12.py").read_text(encoding="utf-8")

    assert '"/v1/agent/runs/{run_id}/cognition"' in api
    assert '"/v1/agent/runs/{run_id}/direction"' in api
    assert '/observe off|focus|balanced|verbose' in terminal
    assert 'line == "/cognition"' in terminal
    assert 'line.startswith("/steer")' in terminal
    assert 'argv[0] == "steer"' in terminal
    assert '_OBSERVE_MODES = {"off", "focus", "balanced", "verbose"}' in ui
    assert "private chain-of-thought" in Path("src/lighthouse/cognitive.py").read_text(encoding="utf-8")


def test_run_wide_auto_survives_cognitive_input_pause():
    cognitive = Path("src/lighthouse/cognitive.py").read_text(encoding="utf-8")

    assert "preserved_auto" in cognitive
    assert "preserved_scope" in cognitive
    assert "auto_confirm=True" in cognitive
    assert "auto_scope=preserved_scope" in cognitive
    assert "provide_input" in cognitive
    assert "auto_scope={}" not in cognitive.split("def provide_input", 1)[1]
