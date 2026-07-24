from __future__ import annotations

import getpass
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Final


CONTROL_KEY_SERVICE: Final = "com.cpym.su.lighthouse.control"
MODEL_KEY_SERVICE: Final = "com.cpym.su.lighthouse.model"


class SecretStoreError(RuntimeError):
    pass


def _account() -> str:
    return getpass.getuser() or os.environ.get("USER") or os.environ.get("USERNAME") or "lighthouse"


def _creation_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0


def _powershell() -> str | None:
    for name in ("powershell.exe", "pwsh.exe", "powershell", "pwsh"):
        executable = shutil.which(name)
        if executable:
            return executable
    return None


def _windows_secret_root() -> Path:
    local = os.environ.get("LOCALAPPDATA", "").strip()
    root = Path(local) if local else Path.home() / "AppData" / "Local"
    return root / "LightHouse" / "secrets"


def _windows_secret_path(service: str, account: str | None = None) -> Path:
    identity = f"{account or _account()}\0{service}".encode("utf-8")
    return _windows_secret_root() / (hashlib.sha256(identity).hexdigest() + ".bin")


def keychain_available() -> bool:
    if sys.platform == "darwin":
        return shutil.which("security") is not None
    if sys.platform == "win32":
        return _powershell() is not None
    return False


def secret_store_name() -> str:
    if sys.platform == "darwin":
        return "macOS Keychain"
    if sys.platform == "win32":
        return "Windows DPAPI"
    return "environment variables"


def _macos_get(service: str, account: str | None = None) -> str | None:
    process = subprocess.run(
        [
            "security",
            "find-generic-password",
            "-a",
            account or _account(),
            "-s",
            service,
            "-w",
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        return None
    value = (process.stdout or "").strip()
    return value or None


def _windows_get(service: str, account: str | None = None) -> str | None:
    path = _windows_secret_path(service, account)
    if not path.is_file():
        return None
    powershell = _powershell()
    if not powershell:
        return None
    script = r"""
$ErrorActionPreference = 'Stop'
$protected = [System.IO.File]::ReadAllBytes($env:LIGHTHOUSE_SECRET_PATH)
$plain = [System.Security.Cryptography.ProtectedData]::Unprotect(
  $protected,
  $null,
  [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)
[Console]::Out.Write([System.Text.Encoding]::UTF8.GetString($plain))
""".strip()
    env = dict(os.environ)
    env["LIGHTHOUSE_SECRET_PATH"] = str(path)
    process = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=env,
        creationflags=_creation_flags(),
    )
    if process.returncode != 0:
        return None
    value = (process.stdout or "").strip()
    return value or None


def keychain_get(service: str, *, account: str | None = None) -> str | None:
    if sys.platform == "darwin" and shutil.which("security") is not None:
        return _macos_get(service, account)
    if sys.platform == "win32":
        return _windows_get(service, account)
    return None


def _macos_set(service: str, value: str, account: str | None = None) -> None:
    process = subprocess.run(
        [
            "security",
            "add-generic-password",
            "-U",
            "-a",
            account or _account(),
            "-s",
            service,
            "-w",
            value,
        ],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if process.returncode != 0:
        message = (process.stderr or process.stdout or "Keychain write failed").strip()
        raise SecretStoreError(message)


def _windows_set(service: str, value: str, account: str | None = None) -> None:
    powershell = _powershell()
    if not powershell:
        raise SecretStoreError("Windows PowerShell is not available")
    path = _windows_secret_path(service, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    script = r"""
$ErrorActionPreference = 'Stop'
$plain = [System.Text.Encoding]::UTF8.GetBytes($env:LIGHTHOUSE_SECRET_VALUE)
$protected = [System.Security.Cryptography.ProtectedData]::Protect(
  $plain,
  $null,
  [System.Security.Cryptography.DataProtectionScope]::CurrentUser
)
[System.IO.File]::WriteAllBytes($env:LIGHTHOUSE_SECRET_PATH, $protected)
""".strip()
    env = dict(os.environ)
    env["LIGHTHOUSE_SECRET_PATH"] = str(path)
    env["LIGHTHOUSE_SECRET_VALUE"] = value
    process = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        env=env,
        creationflags=_creation_flags(),
    )
    if process.returncode != 0:
        message = (process.stderr or process.stdout or "Windows DPAPI write failed").strip()
        raise SecretStoreError(message)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def keychain_set(service: str, value: str, *, account: str | None = None) -> None:
    value = str(value or "")
    if not value:
        raise ValueError("secret value cannot be empty")
    if sys.platform == "darwin" and shutil.which("security") is not None:
        _macos_set(service, value, account)
        return
    if sys.platform == "win32":
        _windows_set(service, value, account)
        return
    raise SecretStoreError("the native operating-system secret store is not available")


def keychain_delete(service: str, *, account: str | None = None) -> bool:
    if sys.platform == "darwin" and shutil.which("security") is not None:
        process = subprocess.run(
            [
                "security",
                "delete-generic-password",
                "-a",
                account or _account(),
                "-s",
                service,
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        return process.returncode == 0
    if sys.platform == "win32":
        path = _windows_secret_path(service, account)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise SecretStoreError(str(exc)) from exc
    return False


def resolve_secret(environment: str, service: str) -> str:
    from_environment = os.environ.get(environment, "").strip()
    if from_environment:
        return from_environment
    return keychain_get(service) or ""


def control_api_key() -> str:
    return resolve_secret("LIGHTHOUSE_API_KEY", CONTROL_KEY_SERVICE)


def model_api_key() -> str:
    return resolve_secret("LIGHTHOUSE_MODEL_API_KEY", MODEL_KEY_SERVICE)
