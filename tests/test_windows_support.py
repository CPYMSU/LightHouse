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


def test_windows_installer_sequences_database_application_and_service():
    script = Path("install-windows.ps1").read_text(encoding="utf-8")
    assert "$MyInvocation.MyCommand.Path" not in script
    assert "install-windows-database.ps1" in script
    assert "install-windows-core.ps1" in script
    assert "install-windows-service.ps1" in script
    assert script.index("-Stage Prepare") < script.index("-Stage Install")
    assert script.index("-Stage Install") < script.index("-Stage Finalize")
    assert "LIGHTHOUSE_BOOTSTRAP_VALIDATE" in script
    assert "powershell.exe" in script


def test_windows_public_installer_refreshes_current_session_command_path():
    script = Path("install-windows.ps1").read_text(encoding="utf-8")
    assert "function Sync-LightHouseCommandPath" in script
    assert "Get-LightHouseInstallRoot" in script
    assert "[Environment]::SetEnvironmentVariable(" in script
    assert "Get-Command lh.cmd" in script
    assert script.count("Sync-LightHouseCommandPath") >= 3
    assert "LightHouse is ready. Run: lh" in script


def test_windows_application_core_never_starts_service_or_waits_for_health():
    script = Path("install-windows-core.ps1").read_text(encoding="utf-8")
    assert "Register-ScheduledTask" not in script
    assert "Start-ScheduledTask" not in script
    assert "Wait-ApiHealth" not in script
    assert "Windows DPAPI" in script
    assert "Model API key" in script
    assert "database preparation did not provide database_url" in script


def test_windows_service_installer_owns_health_and_diagnostics():
    script = Path("install-windows-service.ps1").read_text(encoding="utf-8")
    assert "Register-ScheduledTask" in script
    assert "Start-ScheduledTask" in script
    assert "Wait-ApiHealth 20" in script
    assert "Wait-ApiHealth 45" in script
    assert "startup-error.log" in script
    assert "startup-direct-error.log" in script
    assert "server-error.log" in script
    assert "Get-ScheduledTaskInfo" in script
    assert "Start-Process" in script
    assert "RedirectStandardOutput" in script
    assert "RedirectStandardError" in script
    assert "& '$escapedPython' -m lighthouse.server *>>" not in script
    assert "migrate" in script
    assert "doctor" in script


def test_windows_private_database_bootstrap_never_requests_postgres_password():
    script = Path("install-windows-database.ps1").read_text(encoding="utf-8")
    assert "Existing PostgreSQL postgres-user password" not in script
    assert "Read-Host" not in script
    assert "initdb.exe" in script
    assert "pg_ctl.exe" in script
    assert "database_managed" in script
    assert "postgres_data_dir" in script
    assert "Private LightHouse Database Kernel" in script
    assert "$DefaultPrivatePort = 55432" in script


def test_windows_uninstaller_only_stops_managed_database():
    script = Path("uninstall-windows.ps1").read_text(encoding="utf-8")
    assert "database_managed" in script
    assert "pg_ctl.exe" in script
    assert "External PostgreSQL installations and databases were left untouched" in script
