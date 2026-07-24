from __future__ import annotations

import subprocess

from lighthouse.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from lighthouse.executors.desktop import DesktopExecutor
from lighthouse.executors.project_file import ProjectFileExecutor
from lighthouse.extra_capabilities import PROJECT_FILE_WRITE_CAPABILITY
from lighthouse.kernel import OperationKernel
from lighthouse.models import KernelMode, OperationRequest, TargetKind
from lighthouse.repository import InMemoryRepository


class Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def test_create_html_then_open_it_through_two_receipts(tmp_path):
    repository = InMemoryRepository()
    system = repository.create_target(
        name="system",
        kind=TargetKind.SYSTEM,
        config={"transport": "local", "default_cwd": str(tmp_path), "allowed_roots": [str(tmp_path)]},
    )
    desktop = repository.create_target(
        name="desktop",
        kind=TargetKind.DESKTOP,
        config={
            "platform": "macos",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "allowed_apps": ["Safari"],
            "allowed_schemes": ["http", "https", "file"],
            "browser": "default",
        },
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=system.id,
        desktop_target_id=desktop.id,
    )
    runner = Runner()
    registry = CapabilityRegistry((*DEFAULT_CAPABILITIES, PROJECT_FILE_WRITE_CAPABILITY))
    kernel = OperationKernel(
        repository,
        registry,
        {
            "project_file": ProjectFileExecutor(),
            "desktop": DesktopExecutor(platform="darwin", runner=runner),
        },
    )

    pending = kernel.submit(
        OperationRequest(
            capability="system.file.write.v1",
            arguments={"path": "dashboard.html", "content": "<h1>LightHouse</h1>"},
            workspace_id=workspace.id,
            actor="adsin",
            mode=KernelMode.AUTO,
        )
    )
    assert pending["operation"]["status"] == "awaiting_confirmation"
    written = kernel.confirm(pending["operation"]["id"], actor="adsin")
    assert written["receipt"]["ok"] is True
    assert (tmp_path / "dashboard.html").exists()

    opened = kernel.submit(
        OperationRequest(
            capability="desktop.file.open.v1",
            arguments={"path": "dashboard.html", "browser": "Safari"},
            workspace_id=workspace.id,
            actor="adsin",
            mode=KernelMode.AUTO,
        )
    )
    assert opened["receipt"]["ok"] is True
    assert runner.calls == [["/usr/bin/open", "-a", "Safari", str((tmp_path / "dashboard.html").resolve())]]
