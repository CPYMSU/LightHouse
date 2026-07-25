from __future__ import annotations

from types import SimpleNamespace

import pytest

from lighthouse.addressing import ExecutionAddressResolver
from lighthouse.capabilities import CapabilityRegistry
from lighthouse.extra_capabilities import DIRECTORY_CREATE_CAPABILITY
from lighthouse.models import TargetKind
from lighthouse.repository import InMemoryRepository


class FakeMemory:
    def conversation_for_run(self, run_id):
        return {"id": "conversation-1"}

    def context(self, **kwargs):
        return {}


def setup_resolver(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    active = desktop / "index.html"
    active.write_text("<html></html>", encoding="utf-8")
    another = tmp_path / "Togetherplan"
    another.mkdir()
    repository = InMemoryRepository()
    target = repository.create_target(
        name="local",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
        },
    )
    workspace = repository.create_workspace(
        name="workspace",
        data_target_id=None,
        system_target_id=target.id,
    )
    run = SimpleNamespace(
        id="run-1",
        workspace_id=workspace.id,
        actor="adsin",
        task="把剛才的 index.html 做得更豐富",
    )
    return ExecutionAddressResolver(FakeMemory(), repository), run, active, another


def test_main_ai_selected_file_is_validated_without_semantic_replacement(tmp_path):
    resolver, run, active, _another = setup_resolver(tmp_path)
    grounded = resolver.ground(
        run=run,
        capability=CapabilityRegistry().get("system.file.read.v1"),
        arguments={"cwd": str(active.parent), "path": "index.html"},
    )
    assert grounded["cwd"] == str(active.parent)
    assert grounded["path"] == "index.html"


def test_real_unindexed_directory_inside_workspace_is_allowed(tmp_path):
    resolver, run, _active, another = setup_resolver(tmp_path)
    grounded = resolver.ground(
        run=run,
        capability=CapabilityRegistry().get("system.shell.exec.v1"),
        arguments={"cwd": str(another), "command": "pwd"},
    )
    assert grounded["cwd"] == str(another)


def test_missing_file_is_rejected_instead_of_replaced_with_active_file(tmp_path):
    resolver, run, _active, _another = setup_resolver(tmp_path)
    with pytest.raises(ValueError, match="does not exist"):
        resolver.ground(
            run=run,
            capability=CapabilityRegistry().get("system.file.read.v1"),
            arguments={"cwd": str(tmp_path), "path": "missing.html"},
        )


def test_outside_workspace_is_rejected(tmp_path):
    resolver, run, _active, _another = setup_resolver(tmp_path)
    outside = tmp_path.parent
    with pytest.raises(PermissionError, match="outside"):
        resolver.ground(
            run=run,
            capability=CapabilityRegistry().get("system.shell.exec.v1"),
            arguments={"cwd": str(outside), "command": "pwd"},
        )


def test_new_directory_discards_model_cwd_and_stays_relative(tmp_path):
    resolver, run, _active, another = setup_resolver(tmp_path)
    grounded = resolver.ground(
        run=run,
        capability=DIRECTORY_CREATE_CAPABILITY,
        arguments={"cwd": str(another), "path": "web", "parents": True},
    )
    assert grounded == {"path": "web", "parents": True}
