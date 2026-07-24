from __future__ import annotations

from pathlib import Path
import subprocess
import sys

import pytest

from lighthouse import secrets
from lighthouse.capabilities import CapabilityRegistry
from lighthouse.executors.desktop import DesktopExecutor
from lighthouse.executors import SystemExecutor
from lighthouse.models import Target, TargetKind
from lighthouse.targets import validate_target_config


class Runner:
    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


def windows_system_target() -> Target:
    config = validate_target_config(
        TargetKind.SYSTEM,
        {
            "transport": "local",
            "platform": "windows",
            "default_cwd": r"C:\work",
            "allowed_roots": [r"C:\work"],
            "shell": "powershell.exe",
        },
    )
    return Target(id="system-win", name="windows", kind=TargetKind.SYSTEM, config=config)


def windows_desktop_target() -> Target:
    config = validate_target_config(
        TargetKind.DESKTOP,
        {
            "platform": "windows",
            "default_cwd": r"C:\work",
            "allowed_roots": [r"C:\work"],
            "allowed_apps": ["explorer.exe", "msedge.exe", "notepad.exe"],
            "allowed_schemes": ["http", "https", "file"],
            "browser": "default",
        },
    )
    return Target(id="desktop-win", name="windows", kind=TargetKind.DESKTOP, config=config)


def test_windows_target_validation_accepts_drive_paths_and_rejects_escape():
    system = windows_system_target()
    assert system.config["platform"] == "windows"
    assert system.config["shell"] == "powershell.exe"

    executor = SystemExecutor().windows
    assert executor._cwd(system, {"cwd": r"C:\work\src"}) == r"C:\work\src"
    with pytest.raises(PermissionError):
        executor._cwd(system, {"cwd": r"D:\outside"})


def test_windows_desktop_uses_start_process_for_url_and_allowlisted_browser():
    runner = Runner()
    executor = DesktopExecutor(
        platform="win32",
        runner=runner,
        path_exists=lambda _path: True,
        path_is_file=lambda _path: True,
    )
    registry = CapabilityRegistry()

    result = executor.execute(
        registry.get("desktop.browser.open_url.v1"),
        windows_desktop_target(),
        {"url": "https://example.com", "browser": "msedge.exe"},
    )

    assert result.ok is True
    assert runner.calls[0][0] == "powershell.exe"
    assert "Start-Process" in runner.calls[0][-1]
    assert "msedge.exe" in runner.calls[0][-1]
    assert "https://example.com" in runner.calls[0][-1]


def test_windows_desktop_confines_files_to_allowed_root():
    executor = DesktopExecutor(
        platform="win32",
        runner=Runner(),
        path_exists=lambda _path: True,
        path_is_file=lambda _path: True,
    )
    capability = CapabilityRegistry().get("desktop.file.open.v1")

    with pytest.raises(PermissionError):
        executor.execute(
            capability,
            windows_desktop_target(),
            {"path": r"D:\outside\secret.txt"},
        )


def test_windows_system_wraps_local_commands_in_powershell(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="## main\n", stderr="")

    monkeypatch.setattr("lighthouse.executors.windows_system.subprocess.run", fake_run)
    result = SystemExecutor().execute(
        CapabilityRegistry().get("system.git.status.v1"),
        windows_system_target(),
        {},
    )

    assert result.ok is True
    assert calls[0][0] == "powershell.exe"
    assert "Set-Location -LiteralPath 'C:\\work'" in calls[0][-1]
    assert "git status --short --branch" in calls[0][-1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DPAPI integration")
def test_windows_dpapi_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    service = "com.cpym.su.lighthouse.test"
    value = "windows-current-user-secret"

    secrets.keychain_set(service, value)

    assert secrets.keychain_get(service) == value
    assert secrets.keychain_delete(service) is True
    assert secrets.keychain_get(service) is None


def test_windows_installer_is_strictmode_safe_bootstrap():
    script = Path("install-windows.ps1").read_text(encoding="utf-8")
    assert "$MyInvocation.MyCommand.Path" not in script
    assert "$CoreCommit = 'f2ae0df9d69144218bcc68cb6538cae1755923fe'" in script
    assert "LIGHTHOUSE_BOOTSTRAP_VALIDATE" in script
    assert "LIGHTHOUSE_INSTALL_FROM_FILE" in script
    assert "powershell.exe" in script
