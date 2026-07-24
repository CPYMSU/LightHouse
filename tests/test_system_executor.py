from __future__ import annotations

import subprocess

import pytest

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.executors.system import SystemExecutor
from lighthouse.models import Target, TargetKind
from lighthouse.targets import validate_target_config


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


@pytest.fixture
def project(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    (tmp_path / "AGENTS.md").write_text("Run pytest before final.\n", encoding="utf-8")
    (tmp_path / "demo.txt").write_text("old\n", encoding="utf-8")
    _git(tmp_path, "add", "AGENTS.md", "demo.txt")
    _git(tmp_path, "commit", "-m", "initial")
    return tmp_path


def target(project):
    config = validate_target_config(
        TargetKind.SYSTEM,
        {
            "transport": "local",
            "default_cwd": str(project),
            "allowed_roots": [str(project)],
            "test_command": "python -c 'print(123)'",
        },
    )
    return Target(id="system-1", name="local", kind=TargetKind.SYSTEM, config=config)


def test_project_context_reads_files_and_instructions(project):
    registry = CapabilityRegistry()
    result = SystemExecutor().execute(
        registry.get("system.project.context.v1"),
        target(project),
        {},
    )
    assert result.ok
    assert "demo.txt" in result.result["files"]
    assert result.result["instructions"][0]["path"] == "AGENTS.md"
    assert "pytest" in result.result["instructions"][0]["content"]


def test_file_read_search_diff_and_patch(project):
    registry = CapabilityRegistry()
    executor = SystemExecutor()
    selected = target(project)

    read = executor.execute(
        registry.get("system.file.read.v1"),
        selected,
        {"path": "demo.txt"},
    )
    assert read.ok
    assert read.result["content"] == "old\n"

    search = executor.execute(
        registry.get("system.file.search.v1"),
        selected,
        {"query": "old"},
    )
    assert search.ok
    assert any("demo.txt" in line for line in search.result["matches"])

    patch = """diff --git a/demo.txt b/demo.txt
--- a/demo.txt
+++ b/demo.txt
@@ -1 +1 @@
-old
+new
"""
    applied = executor.execute(
        registry.get("system.file.patch.v1"),
        selected,
        {"patch": patch},
    )
    assert applied.ok

    diff = executor.execute(
        registry.get("system.git.diff.v1"),
        selected,
        {},
    )
    assert diff.ok
    assert "+new" in diff.result["diff"]


def test_test_runner_uses_configured_command(project):
    result = SystemExecutor().execute(
        CapabilityRegistry().get("system.test.run.v1"),
        target(project),
        {},
    )
    assert result.ok
    assert "123" in result.result["stdout"]


def test_cwd_and_path_escape_are_rejected(project):
    executor = SystemExecutor()
    selected = target(project)
    with pytest.raises(PermissionError):
        executor.execute(
            CapabilityRegistry().get("system.git.status.v1"),
            selected,
            {"cwd": "/tmp"},
        )
    with pytest.raises(ValueError):
        executor.execute(
            CapabilityRegistry().get("system.file.read.v1"),
            selected,
            {"path": "../secret"},
        )
