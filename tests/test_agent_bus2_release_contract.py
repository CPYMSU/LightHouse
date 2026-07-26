from pathlib import Path


def test_agent_bus2_reuses_the_existing_durable_runtime():
    bootstrap = Path("src/lighthouse/bootstrap.py").read_text(encoding="utf-8")
    brain = Path("src/lighthouse/mega_brain.py").read_text(encoding="utf-8")
    registry = Path("src/lighthouse/agent_registry.py").read_text(encoding="utf-8")
    intensity_provider = Path("src/lighthouse/intensity_provider.py").read_text(encoding="utf-8")
    migration = Path("src/lighthouse/bootstrap.py").read_text(encoding="utf-8")

    assert "AgentBus2Registry" in bootstrap
    assert "IntensityAwareAgentBusProvider" in bootstrap
    assert "class IntensityAwareAgentBusProvider(AgentBusStructuredProvider)" in intensity_provider
    assert "WorkIntensityMixin" in brain
    assert "AgentResultFusionMixin" in brain
    assert "AgentExecutionContextMixin" in brain
    assert "CognitiveContinuityMixin" in brain
    assert "AdaptiveEngineeringMixin" in brain
    assert "design-agent','coding-agent','verification-agent" in registry
    assert "active=FALSE" in registry
    assert '"0009_persistent_emergent_personality.sql"' in migration
    assert '"0010_' not in migration


def test_agent_bus2_wildcard_and_boundary_checks_are_both_present():
    worker = Path("src/lighthouse/background_intelligence.py").read_text(encoding="utf-8")
    assert '"*" not in allowed and capability_name not in allowed' in worker
    assert 'scope.get("workspace_id") != work_order.get("workspace_id")' in worker
    assert 'operation.get("target_id") not in target_ids' in worker
    assert 'operation.get("kernel") not in kernels' in worker
    assert "Massive Build write requires an active non-overlapping Write Lease" in worker


def test_agent_bus2_has_structured_assignments_findings_conflicts_and_result_fusion():
    coordination = Path("src/lighthouse/agent_coordination.py").read_text(encoding="utf-8")
    bus = Path("src/lighthouse/scalable_agent_bus.py").read_text(encoding="utf-8")
    registry = Path("src/lighthouse/agent_registry.py").read_text(encoding="utf-8")
    results = Path("src/lighthouse/agent_results.py").read_text(encoding="utf-8")
    capabilities = Path("src/lighthouse/agent_capabilities.py").read_text(encoding="utf-8")

    assert "shared_cognitive_brief" in coordination
    assert "local_cognitive_state" in coordination
    assert "dedupe_key" in coordination
    assert "work_deduplicated" in bus
    assert "finding_published" in bus
    assert "write_intent_acquired" in bus
    assert "agent_conflict" in bus
    assert "_semantic_duplicate" in registry
    assert "quality_profiles" in registry
    assert "normalise_agent_result" in results
    assert "fuse_agent_results" in results
    assert "agent.bus.findings.v1" in capabilities
    assert "agent.bus.conflicts.v1" in capabilities


def test_work_intensity_is_separate_from_observe_auto_and_kernel():
    intensity = Path("src/lighthouse/work_intensity.py").read_text(encoding="utf-8")
    provider = Path("src/lighthouse/intensity_provider.py").read_text(encoding="utf-8")
    terminal = Path("src/lighthouse/terminal_v4.py").read_text(encoding="utf-8")
    api = Path("src/lighthouse/api.py").read_text(encoding="utf-8")

    for mode in ("quick", "balanced", "advanced", "extreme"):
        assert f'"{mode}"' in intensity
    assert "WORK_INTENSITY_KEY" in terminal
    assert "/intensity quick|balanced|advanced|extreme" in terminal
    assert '"work_intensity": policy.name' in terminal
    assert "work_intensity" in api
    assert 'payload["reasoning_effort"]' in provider
    assert "reasoning_effort_fallback" in provider
    assert "observe_mode" not in intensity
    assert "auto_confirm" not in POLICIES_TEXT


POLICIES_TEXT = Path("src/lighthouse/work_intensity.py").read_text(encoding="utf-8").split("class WorkIntensityMixin", 1)[0]
