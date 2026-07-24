from __future__ import annotations

from dataclasses import dataclass
import os


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
        database_url = os.environ.get("LIGHTHOUSE_DATABASE_URL", "").strip()
        api_key = os.environ.get("LIGHTHOUSE_API_KEY", "").strip()
        if not database_url:
            raise RuntimeError("LIGHTHOUSE_DATABASE_URL is required")
        if len(api_key) < 16:
            raise RuntimeError("LIGHTHOUSE_API_KEY must contain at least 16 characters")
        json_mode = os.environ.get("LIGHTHOUSE_MODEL_JSON_MODE", "1").strip().lower()
        return cls(
            database_url=database_url,
            api_key=api_key,
            host=os.environ.get("LIGHTHOUSE_HOST", "127.0.0.1"),
            port=int(os.environ.get("LIGHTHOUSE_PORT", "8787")),
            model_base_url=os.environ.get("LIGHTHOUSE_MODEL_BASE_URL", "").strip(),
            model_api_key=os.environ.get("LIGHTHOUSE_MODEL_API_KEY", "").strip(),
            model=os.environ.get("LIGHTHOUSE_MODEL", "").strip(),
            model_timeout=int(os.environ.get("LIGHTHOUSE_MODEL_TIMEOUT", "120")),
            model_json_mode=json_mode not in {"0", "false", "off", "no"},
            model_max_state_chars=int(
                os.environ.get("LIGHTHOUSE_MODEL_MAX_STATE_CHARS", "120000")
            ),
        )
