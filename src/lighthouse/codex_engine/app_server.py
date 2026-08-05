from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
from threading import Event, Lock, Thread
import time
from typing import Any, Callable, Protocol

from .protocol import (
    CodexProtocolError,
    is_notification,
    is_response,
    is_server_request,
    make_notification,
    make_request,
    make_response,
    parse_message,
)


class CodexAppServerError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, data: object = None):
        super().__init__(message)
        self.code = code
        self.data = data


class JsonLineTransport(Protocol):
    def start(self) -> None: ...
    def send(self, message: dict[str, Any]) -> None: ...
    def receive(self, timeout: float | None = None) -> dict[str, Any] | None: ...
    def close(self) -> None: ...
    def diagnostics(self) -> list[str]: ...


class ProcessJsonLineTransport:
    """Stdio JSONL transport for ``codex app-server --stdio``."""

    def __init__(
        self,
        *,
        binary: str = "codex",
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ):
        self.binary = binary
        self.cwd = cwd
        self.env = dict(env or {})
        self.process: subprocess.Popen[str] | None = None
        self.incoming: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self.stderr_lines: deque[str] = deque(maxlen=200)
        self.write_lock = Lock()
        self.reader: Thread | None = None
        self.stderr_reader: Thread | None = None

    def start(self) -> None:
        if self.process is not None and self.process.poll() is None:
            return
        resolved = shutil.which(self.binary) if not Path(self.binary).is_file() else self.binary
        if not resolved:
            raise CodexAppServerError(
                f"Codex executable not found: {self.binary}. Install Codex CLI or set LIGHTHOUSE_CODEX_BINARY."
            )
        environment = os.environ.copy()
        environment.update(self.env)
        self.process = subprocess.Popen(
            [resolved, "app-server", "--stdio"],
            cwd=self.cwd,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        self.reader = Thread(target=self._read_stdout, name="codex-app-server-reader", daemon=True)
        self.stderr_reader = Thread(target=self._read_stderr, name="codex-app-server-stderr", daemon=True)
        self.reader.start()
        self.stderr_reader.start()

    def _read_stdout(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            for line in self.process.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    self.incoming.put(parse_message(line))
                except BaseException as exc:
                    self.incoming.put(exc)
        finally:
            self.incoming.put(None)

    def _read_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        for line in self.process.stderr:
            self.stderr_lines.append(line.rstrip())

    def send(self, message: dict[str, Any]) -> None:
        process = self.process
        if process is None or process.poll() is not None or process.stdin is None:
            raise CodexAppServerError("Codex app-server is not running")
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.write_lock:
            process.stdin.write(encoded)
            process.stdin.flush()

    def receive(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            value = self.incoming.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(value, BaseException):
            raise CodexAppServerError(str(value)) from value
        if value is None:
            process = self.process
            code = process.poll() if process else None
            diagnostics = "\n".join(self.stderr_lines)[-4000:]
            raise CodexAppServerError(
                f"Codex app-server closed unexpectedly (exit={code})"
                + (f": {diagnostics}" if diagnostics else "")
            )
        return value

    def close(self) -> None:
        process = self.process
        self.process = None
        if process is None:
            return
        try:
            if process.stdin:
                process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
        for stream in (process.stdout, process.stderr):
            try:
                if stream:
                    stream.close()
            except OSError:
                pass

    def diagnostics(self) -> list[str]:
        return list(self.stderr_lines)


@dataclass
class _Pending:
    ready: Event
    result: object = None
    error: CodexAppServerError | None = None


class CodexAppServerClient:
    """Thread-safe Codex app-server v2 client with approval request support."""

    def __init__(
        self,
        *,
        transport: JsonLineTransport | None = None,
        binary: str = "codex",
        cwd: str | None = None,
        request_timeout: float = 120.0,
        client_name: str = "lighthouse",
        client_version: str = "1.8.0",
        experimental_api: bool = True,
    ):
        self.transport = transport or ProcessJsonLineTransport(binary=binary, cwd=cwd)
        self.request_timeout = float(request_timeout)
        self.client_name = client_name
        self.client_version = client_version
        self.experimental_api = experimental_api
        self._next_id = 1
        self._id_lock = Lock()
        self._pending: dict[int, _Pending] = {}
        self._pending_lock = Lock()
        self.notifications: queue.Queue[dict[str, Any]] = queue.Queue()
        self.server_requests: queue.Queue[dict[str, Any]] = queue.Queue()
        self._closed = Event()
        self._reader: Thread | None = None
        self.initialized = False

    def start(self) -> dict[str, Any]:
        if self._reader is not None and self._reader.is_alive():
            return {}
        self.transport.start()
        self._reader = Thread(target=self._read_loop, name="lighthouse-codex-jsonrpc", daemon=True)
        self._reader.start()
        result = self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": self.client_name,
                    "title": "LightHouse Code Engine",
                    "version": self.client_version,
                },
                "capabilities": {
                    "experimentalApi": self.experimental_api,
                    "extensions": {"openai/form": {}},
                },
            },
        )
        self.notify("initialized", {})
        self.initialized = True
        return result

    def _allocate_id(self) -> int:
        with self._id_lock:
            value = self._next_id
            self._next_id += 1
            return value

    def _read_loop(self) -> None:
        try:
            while not self._closed.is_set():
                message = self.transport.receive(timeout=0.25)
                if message is None:
                    continue
                if is_response(message):
                    self._resolve(message)
                elif is_server_request(message):
                    self.server_requests.put(message)
                elif is_notification(message):
                    self.notifications.put(message)
        except BaseException as exc:
            failure = exc if isinstance(exc, CodexAppServerError) else CodexAppServerError(str(exc))
            with self._pending_lock:
                pending = list(self._pending.values())
                self._pending.clear()
            for item in pending:
                item.error = failure
                item.ready.set()
            self._closed.set()

    def _resolve(self, message: dict[str, Any]) -> None:
        try:
            request_id = int(message["id"])
        except (KeyError, TypeError, ValueError):
            return
        with self._pending_lock:
            pending = self._pending.pop(request_id, None)
        if pending is None:
            return
        if isinstance(message.get("error"), dict):
            error = message["error"]
            pending.error = CodexAppServerError(
                str(error.get("message") or "Codex request failed"),
                code=int(error["code"]) if isinstance(error.get("code"), int) else None,
                data=error.get("data"),
            )
        else:
            pending.result = message.get("result")
        pending.ready.set()

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
        retries: int = 2,
    ) -> dict[str, Any]:
        for attempt in range(max(0, retries) + 1):
            request_id = self._allocate_id()
            pending = _Pending(ready=Event())
            with self._pending_lock:
                self._pending[request_id] = pending
            self.transport.send(make_request(request_id, method, params))
            if not pending.ready.wait(timeout or self.request_timeout):
                with self._pending_lock:
                    self._pending.pop(request_id, None)
                raise CodexAppServerError(f"Codex request timed out: {method}")
            if pending.error is None:
                return dict(pending.result or {}) if isinstance(pending.result, dict) else {"value": pending.result}
            if pending.error.code == -32001 and attempt < retries:
                time.sleep(min(2.0, 0.2 * (2**attempt)))
                continue
            raise pending.error
        raise AssertionError("unreachable")

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.transport.send(make_notification(method, params))

    def respond(self, request_id: int | str, result: dict[str, Any]) -> None:
        self.transport.send(make_response(request_id, result))

    def next_notification(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return self.notifications.get(timeout=timeout)
        except queue.Empty:
            return None

    def next_server_request(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return self.server_requests.get(timeout=timeout)
        except queue.Empty:
            return None

    def thread_start(self, *, cwd: str, model: str | None = None, policy: dict[str, Any] | None = None, ephemeral: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {"cwd": cwd, "ephemeral": ephemeral}
        if model:
            params["model"] = model
        params.update(policy or {})
        return self.request("thread/start", params)

    def thread_resume(self, thread_id: str, *, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("thread/resume", {"threadId": thread_id, **(policy or {})})

    def thread_fork(self, thread_id: str, *, last_turn_id: str | None = None, ephemeral: bool = False) -> dict[str, Any]:
        params: dict[str, Any] = {"threadId": thread_id, "ephemeral": ephemeral}
        if last_turn_id:
            params["lastTurnId"] = last_turn_id
        return self.request("thread/fork", params)

    def thread_read(self, thread_id: str, *, include_turns: bool = True) -> dict[str, Any]:
        return self.request("thread/read", {"threadId": thread_id, "includeTurns": include_turns})

    def thread_list(self, *, cursor: str | None = None, limit: int = 50, **filters: Any) -> dict[str, Any]:
        params = {"limit": limit, **filters}
        if cursor:
            params["cursor"] = cursor
        return self.request("thread/list", params)

    def thread_compact(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/compact/start", {"threadId": thread_id})

    def turn_start(
        self,
        thread_id: str,
        text: str,
        *,
        cwd: str | None = None,
        model: str | None = None,
        policy: dict[str, Any] | None = None,
        output_schema: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "threadId": thread_id,
            "input": [{"type": "text", "text": text}],
        }
        if cwd:
            params["cwd"] = cwd
        if model:
            params["model"] = model
        if output_schema:
            params["outputSchema"] = output_schema
        params.update(policy or {})
        return self.request("turn/start", params)

    def turn_steer(self, thread_id: str, text: str) -> dict[str, Any]:
        return self.request(
            "turn/steer",
            {"threadId": thread_id, "input": [{"type": "text", "text": text}]},
        )

    def turn_interrupt(self, thread_id: str, turn_id: str) -> dict[str, Any]:
        return self.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})

    def review_start(self, thread_id: str, *, target: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.request("review/start", {"threadId": thread_id, **(target or {})})

    def command_exec(
        self,
        command: list[str],
        *,
        cwd: str,
        policy: dict[str, Any] | None = None,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"command": command, "cwd": cwd}
        if timeout_ms is not None:
            params["timeoutMs"] = int(timeout_ms)
        params.update(policy or {})
        return self.request("command/exec", params)

    def close(self) -> None:
        self._closed.set()
        self.transport.close()
        reader = self._reader
        if reader and reader.is_alive():
            reader.join(timeout=1)

    def diagnostics(self) -> list[str]:
        return self.transport.diagnostics()

    def __enter__(self) -> "CodexAppServerClient":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
