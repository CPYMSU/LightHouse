from __future__ import annotations

from lighthouse.background_intelligence import BackgroundIntelligenceWorker


def test_run_wide_auto_wildcard_keeps_workspace_target_and_kernel_boundaries():
    work = {"workspace_id": "workspace-1"}
    scope = {
        "workspace_id": "workspace-1",
        "allowed_capabilities": ["*"],
        "target_ids": ["target-1"],
        "allowed_kernels": ["system"],
    }
    assert BackgroundIntelligenceWorker._scope_allows(
        scope,
        work,
        "system.file.patch.v1",
        operation={"target_id": "target-1", "kernel": "system"},
    )
    assert not BackgroundIntelligenceWorker._scope_allows(
        scope,
        {"workspace_id": "workspace-2"},
        "system.file.patch.v1",
        operation={"target_id": "target-1", "kernel": "system"},
    )
    assert not BackgroundIntelligenceWorker._scope_allows(
        scope,
        work,
        "system.file.patch.v1",
        operation={"target_id": "target-2", "kernel": "system"},
    )
    assert not BackgroundIntelligenceWorker._scope_allows(
        scope,
        work,
        "system.file.patch.v1",
        operation={"target_id": "target-1", "kernel": "data"},
    )


def test_exact_agent_capability_scope_still_works_without_wildcard():
    scope = {
        "workspace_id": "workspace-1",
        "allowed_capabilities": ["system.test.run.v1"],
    }
    work = {"workspace_id": "workspace-1"}
    assert BackgroundIntelligenceWorker._scope_allows(
        scope,
        work,
        "system.test.run.v1",
    )
    assert not BackgroundIntelligenceWorker._scope_allows(
        scope,
        work,
        "system.file.patch.v1",
    )
