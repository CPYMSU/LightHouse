from pathlib import Path


def test_dispatch_many_supports_dynamic_dependencies_and_deduplication():
    source = Path("src/lighthouse/executors/elastic_agent_bus.py").read_text(encoding="utf-8")
    assert 'raw.get("depends_on")' in source
    assert 'text.startswith("batch:")' in source
    assert "add_dependencies" in source
    assert '"collaboration_graph": True' in source
    assert '"deduplicated": deduplicated' in source


def test_agent_collaboration_is_bounded_by_work_intensity():
    worker = Path("src/lighthouse/background_intelligence.py").read_text(encoding="utf-8")
    intensity = Path("src/lighthouse/work_intensity.py").read_text(encoding="utf-8")
    assert "current_depth >= policy.collaboration_depth" in worker
    assert '"collaboration_requested"' in worker
    assert "normalise_collaboration_requests" in worker
    assert 'collaboration_depth=0' in intensity
    assert 'collaboration_depth=3' in intensity


def test_agent_loop_is_elastic_but_stops_without_new_evidence():
    worker = Path("src/lighthouse/background_intelligence.py").read_text(encoding="utf-8")
    assert "policy.agent_initial_rounds" in worker
    assert "policy.agent_hard_rounds" in worker
    assert "policy.agent_extension_rounds" in worker
    assert "no_progress_rounds >= 2" in worker
    assert '"budget_extended"' in worker
    assert "seen_calls" in worker
