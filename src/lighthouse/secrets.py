from __future__ import annotations

import ctypes
from ctypes import wintypes
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
_CRYPTPROTECT_UI_FORBIDDEN: Final = 0x1


class SecretStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _account() -> str:
    return getpass.getuser() or os.environ.get("USER") or os.environ.get("USERNAME") or "lighthouse"


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
    return sys.platform == "win32"


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


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    return _DataBlob(len(data), pointer), buffer


def _windows_protect(value: str) -> bytes:
    if sys.platform != "win32":
        raise SecretStoreError("Windows DPAPI is not available")
    plain, _buffer = _blob(value.encode("utf-8"))
    protected = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(plain),
        "LightHouse OS",
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(protected),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(protected.pbData, protected.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(protected.pbData)


def _windows_unprotect(data: bytes) -> str:
    if sys.platform != "win32":
        raise SecretStoreError("Windows DPAPI is not available")
    protected, _buffer = _blob(data)
    plain = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        wintypes.LPVOID,
        wintypes.LPVOID,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(protected),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(plain),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(plain.pbData, plain.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(plain.pbData)


def _windows_get(service: str, account: str | None = None) -> str | None:
    path = _windows_secret_path(service, account)
    if not path.is_file():
        return None
    try:
        value = _windows_unprotect(path.read_bytes()).strip()
    except (OSError, UnicodeError):
        return None
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
    path = _windows_secret_path(service, account)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    try:
        temporary.write_bytes(_windows_protect(value))
        os.replace(temporary, path)
    except OSError as exc:
        raise SecretStoreError(str(exc)) from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
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
