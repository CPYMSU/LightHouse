from __future__ import annotations

import subprocess

from lighthouse import secrets


def test_environment_secret_has_priority(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_API_KEY", "environment-secret")
    monkeypatch.setattr(secrets, "keychain_get", lambda _service: "keychain-secret")
    assert secrets.control_api_key() == "environment-secret"


def test_keychain_get_returns_none_on_failure(monkeypatch):
    monkeypatch.setattr(secrets, "keychain_available", lambda: True)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Result", (), {"returncode": 44, "stdout": "", "stderr": "missing"}
        )(),
    )
    assert secrets.keychain_get("example") is None
