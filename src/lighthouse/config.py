from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str
    api_key: str
    host: str = "127.0.0.1"
    port: int = 8787

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("LIGHTHOUSE_DATABASE_URL", "").strip()
        api_key = os.environ.get("LIGHTHOUSE_API_KEY", "").strip()
        if not database_url:
            raise RuntimeError("LIGHTHOUSE_DATABASE_URL is required")
        if len(api_key) < 16:
            raise RuntimeError("LIGHTHOUSE_API_KEY must contain at least 16 characters")
        return cls(database_url=database_url, api_key=api_key, host=os.environ.get("LIGHTHOUSE_HOST", "127.0.0.1"), port=int(os.environ.get("LIGHTHOUSE_PORT", "8787")))
