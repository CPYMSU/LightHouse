from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit

import httpx


_LABEL = "com.cpym.su.lighthouse"


def _config_path() -> Path:
    return Path(
        os.environ.get("LIGHTHOUSE_CONFIG")
        or Path.home() / ".lighthouse" / "config.json"
    ).expanduser()


def _read_config() -> dict[str, object]:
    try:
        value = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _matching_default_instance(base_url: str) -> bool:
    try:
        requested = urlsplit(base_url)
        if requested.hostname not in {"localhost", "127.0.0.1", "::1"}:
            return False
        config = _read_config()
        if str(config.get("instance_id") or "default") != "default":
            return False
        configured_url = str(config.get("url") or "http://127.0.0.1:8787")
        configured = urlsplit(configured_url)
        requested_port = requested.port or (443 if requested.scheme == "https" else 80)
        configured_port = configured.port or int(config.get("port") or 8787)
        return requested_port == configured_port
    except (TypeError, ValueError):
        return False


def _healthy(base_url: str, timeout: float = 0.75) -> bool:
    try:
        with httpx.Client(timeout=timeout, trust_env=False) as client:
            response = client.get(base_url.rstrip("/") + "/healthz")
        if response.status_code != 200:
            return False
        value = response.json()
    except Exception:
        return False
    return isinstance(value, dict) and value.get("status") == "ok"


def _wait_until_healthy(base_url: str, timeout: float = 12.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _healthy(base_url):
            return True
        time.sleep(0.25)
    return False


def _run(command: list[str]) -> int:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=20,
            creationflags=(
                int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
                if os.name == "nt"
                else 0
            ),
        )
    except (OSError, subprocess.SubprocessError):
        return 1
    return int(completed.returncode)


def _recover_macos(base_url: str) -> bool:
    plist = Path.home() / "Library" / "LaunchAgents" / f"{_LABEL}.plist"
    if not plist.is_file():
        return False
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{_LABEL}"

    _run(["launchctl", "kickstart", "-k", target])
    if _wait_until_healthy(base_url, 6.0):
        return True

    _run(["launchctl", "bootout", target])
    _run(["launchctl", "bootout", domain, str(plist)])
    if _run(["launchctl", "bootstrap", domain, str(plist)]) != 0:
        return False
    _run(["launchctl", "kickstart", "-k", target])
    return _wait_until_healthy(base_url)


def _recover_windows(base_url: str) -> bool:
    _run(["schtasks.exe", "/Run", "/TN", "LightHouse"])
    return _wait_until_healthy(base_url)


def recover_local_service(base_url: str) -> bool:
    """Best-effort recovery for the installed default loopback service.

    The function never changes configuration or authority. It only wakes the platform
    service that owns the configured default instance, then verifies `/healthz`.
    """

    try:
        if not _matching_default_instance(base_url):
            return False
        if _healthy(base_url):
            return True
        if sys.platform == "darwin":
            return _recover_macos(base_url)
        if os.name == "nt":
            return _recover_windows(base_url)
    except Exception:
        return False
    return False
