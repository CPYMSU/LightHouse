from __future__ import annotations

from types import SimpleNamespace

from lighthouse.conversation_control import (
    PENDING_NEW_CONVERSATION_KEY,
    install_terminal_hooks,
)


def test_new_conversation_intent_is_preserved_until_success():
    saved = {
        "conversation_id": "conversation-old",
        "last_run_id": "run-old",
        "workspace": "workspace-1",
    }
    calls = []

    def config():
        return None, dict(saved)

    def save(value):
        saved.clear()
        saved.update(value)

    def run_task(task, **kwargs):
        calls.append((task, dict(kwargs)))
        assert kwargs["new_conversation"] is True
        kwargs["config"]["conversation_id"] = "conversation-new"
        kwargs["config"]["last_run_id"] = "run-new"
        return 0

    base = SimpleNamespace(_config=config, _save=save)
    terminal = SimpleNamespace(run_task=run_task)
    install_terminal_hooks(base, terminal)

    # This mirrors terminal_v4's `/new` branch.
    current = dict(saved)
    current.pop("conversation_id")
    current.pop("last_run_id")
    base._save(current)

    assert saved[PENDING_NEW_CONVERSATION_KEY] is True
    terminal.run_task("你好", config=saved)

    assert calls[0][0] == "你好"
    assert calls[0][1]["new_conversation"] is True
    assert saved["conversation_id"] == "conversation-new"
    assert saved["last_run_id"] == "run-new"
    assert PENDING_NEW_CONVERSATION_KEY not in saved


def test_normal_run_does_not_force_new_conversation():
    saved = {
        "conversation_id": "conversation-existing",
        "last_run_id": "run-existing",
    }
    calls = []

    def config():
        return None, dict(saved)

    def save(value):
        saved.clear()
        saved.update(value)

    def run_task(task, **kwargs):
        calls.append(kwargs)
        return 0

    base = SimpleNamespace(_config=config, _save=save)
    terminal = SimpleNamespace(run_task=run_task)
    install_terminal_hooks(base, terminal)

    terminal.run_task("继续", config=saved)

    assert "new_conversation" not in calls[0]
    assert saved["conversation_id"] == "conversation-existing"
