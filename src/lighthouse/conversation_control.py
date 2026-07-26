from __future__ import annotations

from typing import Any, Callable


PENDING_NEW_CONVERSATION_KEY = "new_conversation_pending"


def install_terminal_hooks(base: Any, terminal: Any) -> None:
    """Make `/new` a durable one-shot intent instead of only clearing a pointer.

    The terminal historically removed ``conversation_id`` and ``last_run_id``. The
    server then received ``conversation_id=None`` with ``new_conversation=False`` and
    legitimately reused the actor's latest Workspace conversation. These hooks retain
    the user's explicit intent until the next Run has successfully created a new
    conversation.
    """

    if getattr(terminal, "_new_conversation_hooks_installed", False):
        return

    original_save: Callable[[dict[str, Any]], None] = base._save
    original_run_task = terminal.run_task

    def save_with_new_intent(config: dict[str, Any]) -> None:
        try:
            _path, previous = base._config()
        except Exception:
            previous = {}
        cleared_active_conversation = (
            bool(previous.get("conversation_id") or previous.get("last_run_id"))
            and not config.get("conversation_id")
            and not config.get("last_run_id")
        )
        if cleared_active_conversation:
            config[PENDING_NEW_CONVERSATION_KEY] = True
        original_save(config)

    def run_task_with_new_intent(*args: Any, **kwargs: Any) -> int:
        config = kwargs.get("config")
        if not isinstance(config, dict):
            _path, config = base._config()
            kwargs["config"] = config
        pending = bool(config.get(PENDING_NEW_CONVERSATION_KEY))
        if pending:
            kwargs["new_conversation"] = True
        result = original_run_task(*args, **kwargs)
        if pending:
            config.pop(PENDING_NEW_CONVERSATION_KEY, None)
            original_save(config)
        return result

    base._save = save_with_new_intent
    terminal.run_task = run_task_with_new_intent
    terminal._new_conversation_hooks_installed = True
