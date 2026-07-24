from __future__ import annotations

from dataclasses import dataclass
import getpass
import json
import os
from pathlib import Path
from typing import Any

from .secrets import control_api_key, model_api_key


def _local_config() -> dict[str, Any]:
    path = Path(os.environ.get("LIGHTHOUSE_CONFIG") or Path.home() / ".lighthouse" / "config.json").expanduser()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    host: str = "127.0.0.1"
    port: int = 8787
    model_base_url: str = ""
    model_api_key: str = ""
    model: str = ""
    model_timeout: int = 120
    model_json_mode: bool = True
    model_max_state_chars: int = 120_000

    @classmethod
    def from_env(cls) -> "Settings":
        local = _local_config()
        default_database = f"postgresql://{getpass.getuser()}@127.0.0.1:5432/lighthouse"
        database_url = os.environ.get("LIGHTHOUSE_DATABASE_URL", "").strip() or str(local.get("database_url") or "").strip() or default_database
        api_key = control_api_key()
        if not database_url:
            raise RuntimeError("LIGHTHOUSE_DATABASE_URL is required")
        if len(api_key) < 16:
            raise RuntimeError("LightHouse control credential is missing; run the macOS installer or set LIGHTHOUSE_API_KEY")
        json_mode_value = os.environ.get("LIGHTHOUSE_MODEL_JSON_MODE") or local.get("model_json_mode") or "1"
        json_mode = str(json_mode_value).strip().lower()
        return cls(
            database_url=database_url,
            api_key=api_key,
            host=os.environ.get("LIGHTHOUSE_HOST", str(local.get("host") or "127.0.0.1")),
            port=int(os.environ.get("LIGHTHOUSE_PORT", str(local.get("port") or "8787"))),
            model_base_url=os.environ.get("LIGHTHOUSE_MODEL_BASE_URL", "").strip() or str(local.get("model_base_url") or "").strip(),
            model_api_key=model_api_key(),
            model=os.environ.get("LIGHTHOUSE_MODEL", "").strip() or str(local.get("model") or "").strip(),
            model_timeout=int(os.environ.get("LIGHTHOUSE_MODEL_TIMEOUT", str(local.get("model_timeout") or "120"))),
            model_json_mode=json_mode not in {"0", "false", "off", "no"},
            model_max_state_chars=int(os.environ.get("LIGHTHOUSE_MODEL_MAX_STATE_CHARS", str(local.get("model_max_state_chars") or "120000"))),
        )
