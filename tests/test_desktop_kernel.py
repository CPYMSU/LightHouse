from __future__ import annotations

import pytest

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.kernel import OperationKernel
from lighthouse.models import ExecutionResult, KernelMode, OperationRequest, TargetKind
from lighthouse.repository import InMemoryRepository


class FakeDesktopExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, capability, target, arguments):
        self.calls.append((capability.tool_name, target.id, arguments))
        return ExecutionResult(ok=True, result={"opened": arguments})


def test_desktop_operation_uses_desktop_target_and_receipt():
    repository = InMemoryRepository()
    system = repository.create_target(name="system", kind=TargetKind.SYSTEM, config={"transport": "local"})
    desktop = repository.create_target(name="desktop", kind=TargetKind.DESKTOP, config={"platform": "macos"})
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=system.id,
        desktop_target_id=desktop.id,
    )
    executor = FakeDesktopExecutor()
    kernel = OperationKernel(repository, CapabilityRegistry(), {"desktop": executor})

    result = kernel.submit(
        OperationRequest(
            capability="desktop.browser.open_url.v1",
            arguments={"url": "https://example.com"},
            workspace_id=workspace.id,
            actor="adsin",
            mode=KernelMode.AUTO,
        )
    )

    assert result["operation"]["kernel"] == "desktop"
    assert result["operation"]["target_id"] == desktop.id
    assert result["receipt"]["ok"] is True
    assert len(executor.calls) == 1


def test_desktop_capability_cannot_run_in_system_only_mode():
    repository = InMemoryRepository()
    desktop = repository.create_target(name="desktop", kind=TargetKind.DESKTOP, config={"platform": "macos"})
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=None,
        desktop_target_id=desktop.id,
    )
    kernel = OperationKernel(repository, CapabilityRegistry(), {"desktop": FakeDesktopExecutor()})

    with pytest.raises(ValueError, match="requires desktop mode"):
        kernel.submit(
            OperationRequest(
                capability="desktop.browser.open_url.v1",
                arguments={"url": "https://example.com"},
                workspace_id=workspace.id,
                actor="adsin",
                mode=KernelMode.SYSTEM,
            )
        )
