from __future__ import annotations

import queue
from threading import Thread
from typing import Any

from lighthouse.codex_engine.app_server import CodexAppServerClient


class FakeTransport:
    def __init__(self):
        self.incoming: queue.Queue[dict[str, Any]] = queue.Queue()
        self.sent: list[dict[str, Any]] = []
        self.closed = False

    def start(self) -> None:
        pass

    def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)
        if message.get("method") == "initialize":
            self.incoming.put({"id": message["id"], "result": {"platformFamily": "unix"}})
        elif message.get("method") == "thread/start":
            self.incoming.put({"id": message["id"], "result": {"thread": {"id": "thr_1"}}})
        elif message.get("method") == "turn/start":
            self.incoming.put({"id": message["id"], "result": {"turn": {"id": "turn_1"}}})

    def receive(self, timeout: float | None = None):
        try:
            return self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self.closed = True

    def diagnostics(self) -> list[str]:
        return []


def test_initialization_thread_and_turn_contract() -> None:
    transport = FakeTransport()
    client = CodexAppServerClient(transport=transport)
    initialized = client.start()
    assert initialized["platformFamily"] == "unix"
    thread = client.thread_start(cwd="/tmp/project")
    turn = client.turn_start("thr_1", "Run tests")
    assert thread["thread"]["id"] == "thr_1"
    assert turn["turn"]["id"] == "turn_1"
    assert any(item.get("method") == "initialized" for item in transport.sent)
    client.close()
