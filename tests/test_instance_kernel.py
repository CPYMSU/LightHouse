from __future__ import annotations

import json
from pathlib import Path
import socket

from lighthouse import instances


class DummyProcess:
    pid = 43210

    def poll(self):
        return None

    def terminate(self):
        return None


def write_base(home: Path, *, port: int = 8787) -> Path:
    config = home / "config.json"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{port}",
                "host": "127.0.0.1",
                "port": port,
                "database_url": "postgresql://lighthouse:test@127.0.0.1:55432/lighthouse",
                "database_managed": True,
                "model_base_url": "https://model.example/v1",
                "model": "lighthouse-default",
                "workspace": "old-workspace",
                "conversation_id": "old-conversation",
                "memory_scanned_workspace": "old-workspace",
            }
        ),
        encoding="utf-8",
    )
    return config


def test_find_free_port_skips_an_occupied_port():
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    occupied = listener.getsockname()[1]
    try:
        selected = instances.find_free_port(occupied)
    finally:
        listener.close()
    assert selected != occupied
    assert selected > 0


def test_default_instance_registry_uses_the_existing_config(tmp_path, monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_HOME", str(tmp_path))
    config_path = write_base(tmp_path, port=8899)

    record = instances.ensure_default_instance()

    assert record.id == "default"
    assert record.port == 8899
    assert Path(record.config_path) == config_path
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["instance_id"] == "default"
    assert saved["instance_kind"] == "system"
    assert (tmp_path / "instances" / "default" / "instance.json").is_file()


def test_new_instance_shares_database_but_isolates_terminal_context(tmp_path, monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_HOME", str(tmp_path))
    write_base(tmp_path)
    monkeypatch.setattr(instances, "find_free_port", lambda start, host="127.0.0.1": 8791)
    monkeypatch.setattr(instances, "_launch", lambda record: DummyProcess())
    monkeypatch.setattr(instances, "_wait_until_healthy", lambda record, process, timeout=30.0: None)

    project = tmp_path / "project"
    project.mkdir()
    record = instances.create_instance("Research", project_path=project)

    assert record.id == "research"
    assert record.port == 8791
    assert record.pid == 43210
    config = json.loads(Path(record.config_path).read_text(encoding="utf-8"))
    assert config["database_url"].endswith("/lighthouse")
    assert config["database_managed"] is True
    assert config["instance_id"] == "research"
    assert config["instance_name"] == "Research"
    assert config["url"] == "http://127.0.0.1:8791"
    assert config["project_path"] == str(project.resolve())
    assert "workspace" not in config
    assert "conversation_id" not in config
    assert "memory_scanned_workspace" not in config


def test_starting_a_stopped_instance_reallocates_a_conflicting_port(tmp_path, monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_HOME", str(tmp_path))
    write_base(tmp_path)
    instance_dir = tmp_path / "instances" / "coding"
    instance_dir.mkdir(parents=True)
    config_path = instance_dir / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "url": "http://127.0.0.1:8788",
                "host": "127.0.0.1",
                "port": 8788,
                "database_url": "postgresql://lighthouse:test@127.0.0.1:55432/lighthouse",
            }
        ),
        encoding="utf-8",
    )
    record = instances.InstanceRecord(
        id="coding",
        name="Coding",
        port=8788,
        url="http://127.0.0.1:8788",
        config_path=str(config_path),
        log_dir=str(instance_dir / "logs"),
        kind="managed",
        platform="test",
        created_at="2026-07-25T00:00:00+00:00",
        stopped_at="2026-07-25T01:00:00+00:00",
    )
    record.save()
    monkeypatch.setattr(instances, "_health", lambda record, timeout=0.75: False)
    monkeypatch.setattr(instances, "port_is_free", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(instances, "find_free_port", lambda start, host="127.0.0.1": 8792)
    monkeypatch.setattr(instances, "_launch", lambda record: DummyProcess())
    monkeypatch.setattr(instances, "_wait_until_healthy", lambda record, process, timeout=30.0: None)

    restarted = instances.start_instance("coding")

    assert restarted.port == 8792
    updated = json.loads(config_path.read_text(encoding="utf-8"))
    assert updated["port"] == 8792
    assert updated["url"] == "http://127.0.0.1:8792"
