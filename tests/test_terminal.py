from __future__ import annotations

from pathlib import Path

from lighthouse import terminal


class FakeClient:
    def __init__(self):
        self.targets = []
        self.workspaces = []

    def request(self, method, path, payload=None):
        if method == "GET" and path == "/v1/targets":
            return {"items": self.targets}
        if method == "GET" and path == "/v1/workspaces":
            return {"items": self.workspaces}
        if method == "POST" and path == "/v1/targets":
            value = {"id": "target-1", **payload}
            self.targets.append(value)
            return value
        if method == "POST" and path == "/v1/workspaces":
            value = {"id": "workspace-1", **payload}
            self.workspaces.append(value)
            return value
        raise AssertionError((method, path, payload))


def test_ensure_workspace_binds_current_project(tmp_path, monkeypatch):
    config = {}
    client = FakeClient()
    monkeypatch.setattr(terminal, "_save", lambda value: None)
    workspace = terminal.ensure_workspace(client, config, Path(tmp_path))
    assert workspace == "workspace-1"
    assert config["workspace"] == "workspace-1"
    assert config["project_path"] == str(tmp_path.resolve())
    assert client.targets[0]["config"]["allowed_roots"] == [str(tmp_path.resolve())]


def test_project_test_command_detects_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert terminal._project_test_command(tmp_path) == "python -m pytest -q"
