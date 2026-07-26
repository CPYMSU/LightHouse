from __future__ import annotations

from lighthouse.terminal_v2 import _drive_run


class FakeClient:
    def __init__(self):
        self.calls = []
        self.get_count = 0

    def request(self, method, path, payload=None):
        self.calls.append((method, path, payload))
        if method == "POST" and path.endswith("/auto-authorize"):
            assert payload == {"actor": "adsin", "background": True}
            return {
                "run": {"id": "run-1", "status": "running", "auto_confirm": True},
                "steps": [
                    {"sequence": 1, "kind": "auto_scope_granted", "payload": {}},
                ],
                "pending_operation": None,
            }
        if method == "GET" and path == "/v1/agent/runs/run-1":
            self.get_count += 1
            if self.get_count == 1:
                return {
                    "run": {"id": "run-1", "status": "running", "auto_confirm": True},
                    "steps": [
                        {"sequence": 1, "kind": "auto_scope_granted", "payload": {}},
                        {
                            "sequence": 2,
                            "kind": "decision",
                            "payload": {
                                "kind": "tool",
                                "capability": "system.file.read.v1",
                                "arguments": {"path": "src/app.py"},
                            },
                        },
                    ],
                    "pending_operation": None,
                }
            return {
                "run": {"id": "run-1", "status": "succeeded", "auto_confirm": False},
                "steps": [
                    {"sequence": 1, "kind": "auto_scope_granted", "payload": {}},
                    {
                        "sequence": 2,
                        "kind": "decision",
                        "payload": {
                            "kind": "tool",
                            "capability": "system.file.read.v1",
                            "arguments": {"path": "src/app.py"},
                        },
                    },
                    {
                        "sequence": 3,
                        "kind": "observation",
                        "payload": {
                            "capability": "system.file.read.v1",
                            "receipt": {"ok": True, "result": {"path": "src/app.py"}},
                        },
                    },
                ],
                "pending_operation": None,
            }
        raise AssertionError(f"unexpected request: {method} {path} {payload}")


class FakeUI:
    def __init__(self):
        self.rendered = []
        self.notices = []
        self.final_snapshot = None

    def render_run(self, snapshot, *, seen=None):
        self.rendered.append(snapshot)
        return set() if seen is None else set(seen)

    def confirmation(self, pending):
        pass

    def permission_choice(self, *, auto_available=True):
        assert auto_available is True
        return "auto"

    def notice(self, title, message, *, tone="cyan"):
        self.notices.append((title, message, tone))

    def final(self, snapshot):
        self.final_snapshot = snapshot


def test_auto_authorization_returns_to_live_polling_instead_of_blocking_one_request():
    client = FakeClient()
    ui = FakeUI()
    initial = {
        "run": {
            "id": "run-1",
            "status": "awaiting_confirmation",
            "auto_confirm": False,
        },
        "steps": [],
        "pending_operation": {"operation": {"id": "operation-1"}},
    }

    result = _drive_run(
        client,
        initial,
        actor="adsin",
        ui=ui,
        auto_mode_available=True,
    )

    assert result["run"]["status"] == "succeeded"
    rendered_statuses = [item["run"]["status"] for item in ui.rendered]
    assert "running" in rendered_statuses
    assert ui.final_snapshot["run"]["status"] == "succeeded"
    paths = [path for _method, path, _payload in client.calls]
    assert "/v1/operations/operation-1/confirm-deferred" not in paths
    assert "/v1/agent/runs/run-1/advance" not in paths
    assert any("every tool start" in message for _title, message, _tone in ui.notices)
