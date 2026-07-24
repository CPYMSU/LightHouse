from __future__ import annotations

import re
from typing import Any

from .models import TargetKind


_ENV = re.compile(r"^[A-Z_][A-Z0-9_]{0,127}$")
_HOST = re.compile(r"^[A-Za-z0-9._:-]{1,255}$")
_DATA_KEYS = {"dsn_env", "read_only"}
_SYSTEM_KEYS = {"transport", "host", "port", "user", "identity_file_env", "default_cwd", "shell", "timeout"}


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
        identity_env = str(config.get("identity_file_env") or "").strip()
        if identity_env and not _ENV.fullmatch(identity_env):
            raise ValueError("identity_file_env is invalid")
        if identity_env:
            config["identity_file_env"] = identity_env
    for field in ("default_cwd", "shell"):
        if field in config:
            item = str(config[field]).strip()
            if not item.startswith("/"):
                raise ValueError(f"{field} must be an absolute path")
            config[field] = item
    if "timeout" in config:
        timeout = int(config["timeout"])
        if not 1 <= timeout <= 3600:
            raise ValueError("timeout must be between 1 and 3600 seconds")
        config["timeout"] = timeout
    return config
