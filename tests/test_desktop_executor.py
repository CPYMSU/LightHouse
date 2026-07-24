from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.executors.desktop import DesktopExecutor
from lighthouse.models import Target, TargetKind


class Runner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def desktop_target(root: Path) -> Target:
    return Target(
        id="desktop-1",
        name="desktop",
        kind=TargetKind.DESKTOP,
        config={
            "platform": "macos",
            "default_cwd": str(root),
            "allowed_roots": [str(root)],
            "allowed_apps": ["Safari", "Google Chrome", "Finder"],
            "allowed_schemes": ["http", "https", "file"],
            "browser": "default",
        },
    )


def test_open_html_uses_macos_launch_services(tmp_path):
    html = tmp_path / "index.html"
    html.write_text("<h1>LightHouse</h1>", encoding="utf-8")
    runner = Runner()
    executor = DesktopExecutor(platform="darwin", runner=runner)
    capability = CapabilityRegistry().get("desktop.file.open.v1")

    result = executor.execute(capability, desktop_target(tmp_path), {"path": "index.html"})

    assert result.ok is True
    assert runner.calls == [["/usr/bin/open", str(html.resolve())]]
    assert result.result["operation"] == "open_file"


def test_open_url_can_select_allowlisted_browser(tmp_path):
    runner = Runner()
    executor = DesktopExecutor(platform="darwin", runner=runner)
    capability = CapabilityRegistry().get("desktop.browser.open_url.v1")

    result = executor.execute(
        capability,
        desktop_target(tmp_path),
        {"url": "https://example.com", "browser": "Safari"},
    )

    assert result.ok is True
    assert runner.calls == [["/usr/bin/open", "-a", "Safari", "https://example.com"]]


def test_desktop_rejects_unapproved_schemes_and_paths(tmp_path):
    executor = DesktopExecutor(platform="darwin", runner=Runner())
    registry = CapabilityRegistry()
    with pytest.raises(PermissionError):
        executor.execute(
            registry.get("desktop.browser.open_url.v1"),
            desktop_target(tmp_path),
            {"url": "javascript:alert(1)"},
        )

    outside = tmp_path.parent / "outside.html"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(PermissionError):
        executor.execute(
            registry.get("desktop.file.open.v1"),
            desktop_target(tmp_path),
            {"path": str(outside)},
        )


def test_desktop_fails_closed_off_macos(tmp_path):
    executor = DesktopExecutor(platform="linux", runner=Runner())
    with pytest.raises(RuntimeError):
        executor.execute(
            CapabilityRegistry().get("desktop.browser.open_url.v1"),
            desktop_target(tmp_path),
            {"url": "https://example.com"},
        )
