from __future__ import annotations

from types import SimpleNamespace

import pytest

from lighthouse.addressing import ExecutionAddressResolver
from lighthouse.capabilities import CapabilityRegistry
from lighthouse.extra_capabilities import DIRECTORY_CREATE_CAPABILITY
from lighthouse.models import TargetKind
from lighthouse.repository import InMemoryRepository


class FakeMemory:
    def __init__(self, active_file):
        self.active_file = active_file

    def conversation_for_run(self, run_id):
        return {"id": "conversation-1"}

    def context(self, **kwargs):
        return {
            "conversation": {"id": "conversation-1", "active_subject_kind": "file", "active_subject_value": str(self.active_file)},
            "active_task": {"subject_kind": "file", "subject": str(self.active_file)},
            "relevant_files": [{"canonical_path": str(self.active_file), "name": self.active_file.name}],
            "recent_locators": [{"kind": "file", "canonical_value": str(self.active_file), "display_value": str(self.active_file)}],
        }


def setup_resolver(tmp_path):
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    active = desktop / "index.html"
    active.write_text("<html></html>", encoding="utf-8")
    random = tmp_path / "Togetherplan"
    random.mkdir()
    repository = InMemoryRepository()
    target = repository.create_target(name="local", kind=TargetKind.SYSTEM, config={"transport": "local", "default_cwd": str(tmp_path), "allowed_roots": [str(tmp_path)]})
    workspace = repository.create_workspace(name="workspace", data_target_id=None, system_target_id=target.id)
    run = SimpleNamespace(id="run-1", workspace_id=workspace.id, actor="adsin", task="把剛才的 index.html 做得更豐富")
    return ExecutionAddressResolver(FakeMemory(active), repository), run, active, random


def test_file_path_is_grounded_to_active_subject(tmp_path):
    resolver, run, active, _random = setup_resolver(tmp_path)
    grounded = resolver.ground(run=run, capability=CapabilityRegistry().get("system.file.read.v1"), arguments={"path": "index.html"})
    assert grounded["cwd"] == str(active.parent)
    assert grounded["path"] == "index.html"


def test_random_existing_cwd_is_rejected_when_not_in_memory(tmp_path):
    resolver, run, _active, random = setup_resolver(tmp_path)
    with pytest.raises(ValueError, match="not observed"):
        resolver.ground(run=run, capability=CapabilityRegistry().get("system.shell.exec.v1"), arguments={"cwd": str(random), "command": "pwd"})


def test_new_directory_discards_model_cwd_and_stays_relative(tmp_path):
    resolver, run, _active, random = setup_resolver(tmp_path)
    grounded = resolver.ground(run=run, capability=DIRECTORY_CREATE_CAPABILITY, arguments={"cwd": str(random), "path": "web", "parents": True})
    assert grounded == {"path": "web", "parents": True}
