from __future__ import annotations

from contextlib import nullcontext

from lighthouse import terminal_v3


class FakeClient:
    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []

    def request(self, method: str, path: str, payload: dict | None = None):
        self.requests.append((method, path, payload))
        if method == "POST" and path == "/v1/agent/runs":
            return {
                "run": {
                    "id": "run-1",
                    "status": "succeeded",
                    "final_message": "done",
                },
                "steps": [],
                "pending_operation": None,
                "conversation": {"id": "conversation-1"},
            }
        raise AssertionError(f"unexpected request: {method} {path}")


class FakeUI:
    def __init__(self):
        self.notices: list[tuple[str, str, str]] = []

    def busy(self, _label: str):
        return nullcontext()

    def task_banner(self, _task: str) -> None:
        return None

    def notice(self, label: str, message: str, *, tone: str = "neutral") -> None:
        self.notices.append((label, message, tone))


def _patch_run_dependencies(monkeypatch):
    monkeypatch.setattr(
        terminal_v3.base,
        "ensure_workspace",
        lambda _client, config, _path: config.setdefault("workspace", "workspace-1"),
    )
    monkeypatch.setattr(
        terminal_v3.durable,
        "_scan_memory",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        terminal_v3.durable,
        "_drive_run",
        lambda _client, snapshot, **_kwargs: snapshot,
    )
    monkeypatch.setattr(terminal_v3.base, "_save", lambda _config: None)


def test_auto_mode_uses_one_scope_confirmation_then_server_auto_confirm(monkeypatch):
    _patch_run_dependencies(monkeypatch)
    monkeypatch.setattr(
        terminal_v3,
        "_ask_auto_mode",
        lambda _ui, **_kwargs: True,
    )
    client = FakeClient()
    config = {
        "workspace": "workspace-1",
        "workspace_name": "test",
        "mode": "auto",
        "actor": "operator",
        "auto_mode": True,
    }

    assert terminal_v3.run_task(
        "complete the whole task",
        client=client,
        config=config,
        ui=FakeUI(),
    ) == 0

    payload = client.requests[0][2]
    assert payload is not None
    assert payload["auto_confirm"] is True
    assert payload["task"] == "complete the whole task"


def test_declining_auto_mode_preserves_exact_operation_confirmations(monkeypatch):
    _patch_run_dependencies(monkeypatch)
    monkeypatch.setattr(
        terminal_v3,
        "_ask_auto_mode",
        lambda _ui, **_kwargs: False,
    )
    client = FakeClient()
    config = {
        "workspace": "workspace-1",
        "mode": "auto",
        "actor": "operator",
        "auto_mode": True,
    }

    terminal_v3.run_task(
        "run manually",
        client=client,
        config=config,
        ui=FakeUI(),
    )

    payload = client.requests[0][2]
    assert payload is not None
    assert payload["auto_confirm"] is False


def test_auto_mode_off_does_not_request_scope_authorization(monkeypatch):
    _patch_run_dependencies(monkeypatch)

    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Auto Mode prompt must not run while disabled")

    monkeypatch.setattr(terminal_v3, "_ask_auto_mode", fail_if_called)
    client = FakeClient()
    config = {
        "workspace": "workspace-1",
        "mode": "auto",
        "actor": "operator",
        "auto_mode": False,
    }

    terminal_v3.run_task(
        "manual task",
        client=client,
        config=config,
        ui=FakeUI(),
    )

    payload = client.requests[0][2]
    assert payload is not None
    assert payload["auto_confirm"] is False


def test_auto_mode_setting_is_persisted(monkeypatch):
    saved: list[dict] = []
    monkeypatch.setattr(
        terminal_v3.base,
        "_save",
        lambda config: saved.append(dict(config)),
    )
    config: dict = {}

    assert terminal_v3.set_auto_mode(config, True) is True
    assert config["auto_mode"] is True
    assert saved[-1]["auto_mode"] is True

    assert terminal_v3.set_auto_mode(config, False) is False
    assert config["auto_mode"] is False
    assert saved[-1]["auto_mode"] is False
