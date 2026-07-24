from __future__ import annotations

import re
from typing import Any

from .models import TargetKind


_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
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


def _absolute_path(value: Any, field: str) -> str:
    item = str(value or "").strip()
    if not item.startswith("/") or "\x00" in item:
        raise ValueError(f"{field} must be an absolute path")
    return item


def validate_target_config(kind: TargetKind, value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("target config must be an object")
    config = dict(value)
    allowed = _DATA_KEYS if kind == TargetKind.DATA else _SYSTEM_KEYS
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
