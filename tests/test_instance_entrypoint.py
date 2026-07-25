from __future__ import annotations

from pathlib import Path

from lighthouse import instance_entry
from lighthouse.instances import InstanceRecord


def record(tmp_path: Path) -> InstanceRecord:
    return InstanceRecord(
        id="research",
        name="Research",
        port=8791,
        url="http://127.0.0.1:8791",
        config_path=str(tmp_path / "config.json"),
        log_dir=str(tmp_path / "logs"),
        kind="managed",
        platform="test",
        created_at="2026-07-25T00:00:00+00:00",
        pid=123,
    )


def test_instance_prefix_activates_config_before_terminal_dispatch(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(instance_entry, "instance_config", lambda name, start_if_needed=True: config)
    calls = []
    monkeypatch.setattr(instance_entry.terminal_entry, "main", lambda argv: calls.append(list(argv)) or 0)

    assert instance_entry.main(["--instance", "research", "doctor"]) == 0
    assert calls == [["doctor"]]
    assert instance_entry.os.environ["LIGHTHOUSE_CONFIG"] == str(config)


def test_new_instance_can_start_without_attaching(tmp_path, monkeypatch, capsys):
    created = record(tmp_path)
    monkeypatch.setattr(instance_entry, "create_instance", lambda *args, **kwargs: created)
    monkeypatch.setattr(created, "public", lambda: {"id": "research", "status": "running"})

    assert instance_entry.main(["new", "Research", "--no-attach"]) == 0
    output = capsys.readouterr().out
    assert '"id": "research"' in output
    assert '"status": "running"' in output


def test_regular_commands_preserve_terminal_entry(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(instance_entry.terminal_entry, "main", lambda argv: calls.append(list(argv)) or 0)

    assert instance_entry.main(["doctor"]) == 0
    assert calls == [["doctor"]]
