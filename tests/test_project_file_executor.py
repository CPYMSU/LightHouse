from __future__ import annotations

import hashlib

import pytest

from lighthouse.executors.project_file import ProjectFileExecutor
from lighthouse.extra_capabilities import PROJECT_FILE_WRITE_CAPABILITY
from lighthouse.models import Target, TargetKind


def system_target(root):
    return Target(
        id="system-1",
        name="system",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "default_cwd": str(root),
            "allowed_roots": [str(root)],
        },
    )


def test_atomic_writer_creates_html_and_returns_hash(tmp_path):
    html = "<!doctype html><title>LightHouse</title>"
    result = ProjectFileExecutor().execute(
        PROJECT_FILE_WRITE_CAPABILITY,
        system_target(tmp_path),
        {"path": "dashboard.html", "content": html},
    )

    assert result.ok is True
    assert (tmp_path / "dashboard.html").read_text(encoding="utf-8") == html
    assert result.result["created"] is True
    assert result.result["sha256"] == hashlib.sha256(html.encode()).hexdigest()


def test_atomic_writer_requires_explicit_overwrite(tmp_path):
    path = tmp_path / "dashboard.html"
    path.write_text("old", encoding="utf-8")
    executor = ProjectFileExecutor()

    with pytest.raises(FileExistsError):
        executor.execute(
            PROJECT_FILE_WRITE_CAPABILITY,
            system_target(tmp_path),
            {"path": "dashboard.html", "content": "new"},
        )

    result = executor.execute(
        PROJECT_FILE_WRITE_CAPABILITY,
        system_target(tmp_path),
        {"path": "dashboard.html", "content": "new", "overwrite": True},
    )
    assert result.result["replaced"] is True
    assert path.read_text(encoding="utf-8") == "new"


def test_atomic_writer_rejects_parent_escape(tmp_path):
    with pytest.raises(ValueError):
        ProjectFileExecutor().execute(
            PROJECT_FILE_WRITE_CAPABILITY,
            system_target(tmp_path),
            {"path": "../outside.html", "content": "no"},
        )
