from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from typing import Final


CONTROL_KEY_SERVICE: Final = "com.cpym.su.lighthouse.control"
MODEL_KEY_SERVICE: Final = "com.cpym.su.lighthouse.model"


class SecretStoreError(RuntimeError):
    pass


def _account() -> str:
    return getpass.getuser() or os.environ.get("USER") or "lighthouse"


def keychain_available() -> bool:
    return sys.platform == "darwin" and shutil.which("security") is not None


def keychain_get(service: str, *, account: str | None = None) -> str | None:
    if not keychain_available():
        return None
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


def keychain_set(service: str, value: str, *, account: str | None = None) -> None:
    if not keychain_available():
        raise SecretStoreError("macOS Keychain is not available")
    value = str(value or "")
    if not value:
        raise ValueError("secret value cannot be empty")
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


def keychain_delete(service: str, *, account: str | None = None) -> bool:
    if not keychain_available():
        return False
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


def resolve_secret(environment: str, service: str) -> str:
    from_environment = os.environ.get(environment, "").strip()
    if from_environment:
        return from_environment
    return keychain_get(service) or ""


def control_api_key() -> str:
    return resolve_secret("LIGHTHOUSE_API_KEY", CONTROL_KEY_SERVICE)


def model_api_key() -> str:
    return resolve_secret("LIGHTHOUSE_MODEL_API_KEY", MODEL_KEY_SERVICE)
