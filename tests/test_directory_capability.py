from __future__ import annotations

import pytest

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.executors.project_file import ProjectFileExecutor
from lighthouse.executors.system import SystemExecutor
from lighthouse.extra_capabilities import DIRECTORY_CREATE_CAPABILITY
from lighthouse.models import Target, TargetKind


def test_typed_directory_creation_is_confined_and_idempotent(tmp_path):
    target = Target(id="target-1", name="local", kind=TargetKind.SYSTEM, config={"transport": "local", "default_cwd": str(tmp_path), "allowed_roots": [str(tmp_path)]})
    executor = ProjectFileExecutor()
    created = executor.execute(DIRECTORY_CREATE_CAPABILITY, target, {"path": "web", "parents": True})
    assert created.ok is True
    assert (tmp_path / "web").is_dir()
    repeated = executor.execute(DIRECTORY_CREATE_CAPABILITY, target, {"path": "web", "parents": True})
    assert repeated.result["already_existed"] is True


def test_shell_mkdir_is_rejected_in_favor_of_typed_capability(tmp_path):
    target = Target(id="target-1", name="local", kind=TargetKind.SYSTEM, config={"transport": "local", "default_cwd": str(tmp_path), "allowed_roots": [str(tmp_path)]})
    capability = CapabilityRegistry().get("system.shell.exec.v1")
    with pytest.raises(ValueError, match="system.directory.create.v1"):
        SystemExecutor().execute(capability, target, {"cwd": str(tmp_path), "command": "mkdir -p web"})
