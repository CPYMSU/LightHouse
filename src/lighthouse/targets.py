from __future__ import annotations

import re
from typing import Any

from .models import TargetKind


_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
_APP = re.compile(r"^[^/\x00\r\n]{1,160}$")
_DATA_KEYS = {"dsn_env", "read_only"}
_SYSTEM_KEYS = {
    "transport",
    "host",
    "port",
    "user",
    "identity_file_env",
    "known_hosts_env",
    "strict_host_key",
    "default_cwd",
    "shell",
    "timeout",
    "max_output_chars",
    "allowed_roots",
    "project_instruction_files",
    "test_command",
}
_DESKTOP_KEYS = {
    "platform",
    "default_cwd",
    "allowed_roots",
    "allowed_apps",
    "allowed_schemes",
    "browser",
}


def _absolute_path(value: Any, field: str) -> str:
    item = str(value or "").strip()
    if not item.startswith("/") or "\x00" in item:
        raise ValueError(f"{field} must be an absolute path")
    return item


def _string_array(value: Any, field: str, *, pattern: re.Pattern[str] | None = None) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a non-empty array")
    items: list[str] = []
    for raw in value:
        item = str(raw or "").strip()
        if not item or "\x00" in item or (pattern and not pattern.fullmatch(item)):
            raise ValueError(f"{field} contains an invalid value")
        items.append(item)
    return items


def validate_target_config(kind: TargetKind, value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("target config must be an object")
    config = dict(value)
    if kind == TargetKind.DATA:
        allowed = _DATA_KEYS
    elif kind == TargetKind.SYSTEM:
        allowed = _SYSTEM_KEYS
    else:
        allowed = _DESKTOP_KEYS
    unknown = sorted(set(config) - allowed)
    if unknown:
        raise ValueError("unsupported target config fields: " + ", ".join(unknown))

    if kind == TargetKind.DATA:
        env_name = str(config.get("dsn_env") or "").strip()
        if not _ENV.fullmatch(env_name):
            raise ValueError("data target requires a valid dsn_env name")
        config["dsn_env"] = env_name
        config["read_only"] = bool(config.get("read_only", False))
        return config

    if kind == TargetKind.DESKTOP:
        platform = str(config.get("platform") or "macos").strip().lower()
        if platform != "macos":
            raise ValueError("desktop target platform must be macos")
        config["platform"] = platform
        config["default_cwd"] = _absolute_path(config.get("default_cwd") or "/", "default_cwd")
        roots = config.get("allowed_roots") or [config["default_cwd"]]
        config["allowed_roots"] = [
            _absolute_path(item, "allowed_roots item") for item in _string_array(roots, "allowed_roots")
        ]
        apps = config.get("allowed_apps") or ["Safari", "Google Chrome", "Firefox", "Arc", "Finder"]
        config["allowed_apps"] = _string_array(apps, "allowed_apps", pattern=_APP)
        schemes = config.get("allowed_schemes") or ["http", "https", "file"]
        normalized_schemes = [str(item).strip().lower() for item in _string_array(schemes, "allowed_schemes")]
        if any(not re.fullmatch(r"[a-z][a-z0-9+.-]{0,31}", item) for item in normalized_schemes):
            raise ValueError("allowed_schemes contains an invalid URL scheme")
        config["allowed_schemes"] = normalized_schemes
        browser = str(config.get("browser") or "default").strip()
        if browser.lower() != "default" and browser not in config["allowed_apps"]:
            raise ValueError("desktop browser must be default or an allowed application")
        config["browser"] = browser or "default"
        return config

    transport = str(config.get("transport") or "local").strip().lower()
    if transport not in {"local", "ssh"}:
        raise ValueError("system target transport must be local or ssh")
    config["transport"] = transport
    if transport == "ssh":
        host = str(config.get("host") or "").strip()
        user = str(config.get("user") or "").strip()
        if not _HOST.fullmatch(host) or not user or any(char.isspace() for char in user):
            raise ValueError("SSH target requires valid host and user")
        config["host"] = host
        config["user"] = user
        port = int(config.get("port") or 22)
        if not 1 <= port <= 65535:
            raise ValueError("SSH port is invalid")
        config["port"] = port
        for field in ("identity_file_env", "known_hosts_env"):
            env_name = str(config.get(field) or "").strip()
            if env_name and not _ENV.fullmatch(env_name):
                raise ValueError(f"{field} is invalid")
            if env_name:
                config[field] = env_name
        config["strict_host_key"] = bool(config.get("strict_host_key", True))

    if "default_cwd" in config:
        config["default_cwd"] = _absolute_path(config["default_cwd"], "default_cwd")
    if "shell" in config:
        config["shell"] = _absolute_path(config["shell"], "shell")
    config.setdefault("shell", "/bin/bash")
    config.setdefault("default_cwd", "/")

    if "timeout" in config:
        timeout = int(config["timeout"])
        if not 1 <= timeout <= 3600:
            raise ValueError("timeout must be between 1 and 3600 seconds")
        config["timeout"] = timeout
    else:
        config["timeout"] = 600

    max_output_chars = int(config.get("max_output_chars") or 131072)
    if not 4096 <= max_output_chars <= 2_000_000:
        raise ValueError("max_output_chars must be between 4096 and 2000000")
    config["max_output_chars"] = max_output_chars

    roots = config.get("allowed_roots") or [config["default_cwd"]]
    if not isinstance(roots, list) or not roots:
        raise ValueError("allowed_roots must be a non-empty array")
    config["allowed_roots"] = [_absolute_path(item, "allowed_roots item") for item in roots]

    instruction_files = config.get("project_instruction_files") or [
        "AGENTS.md",
        "AGENTS.override.md",
        "LIGHTHOUSE.md",
        ".lighthouse/project.yaml",
    ]
    if not isinstance(instruction_files, list):
        raise ValueError("project_instruction_files must be an array")
    normalized_files: list[str] = []
    for item in instruction_files:
        name = str(item or "").strip().replace("\\", "/")
        if not name or name.startswith("/") or "\x00" in name or ".." in name.split("/"):
            raise ValueError("project instruction file must be a safe relative path")
        normalized_files.append(name)
    config["project_instruction_files"] = normalized_files

    if "test_command" in config:
        command = str(config["test_command"] or "").strip()
        if not command:
            raise ValueError("test_command cannot be empty")
        config["test_command"] = command
    return config
