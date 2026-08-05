from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
from threading import Event, Lock, Thread
from typing import Any
from uuid import uuid4


class RustKernelError(RuntimeError):
    pass


class RustCodeKernelClient:
    """JSONL client for the optional native Rust PTY/sandbox sidecar."""

    def __init__(self, binary: str = "lighthouse-code-kernel"):
        self.binary = binary
        self.process: subprocess.Popen[str] | None = None
        self.pending: dict[str, tuple[Event, dict[str, Any]]] = {}
        self.pending_lock = Lock()
        self.write_lock = Lock()
        self.events: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr: deque[str] = deque(maxlen=100)
        self.reader: Thread | None = None

    def available(self) -> bool:
        return bool(shutil.which(self.binary) or Path(self.binary).is_file())

    def start(self) -> dict[str, Any]:
        if self.process and self.process.poll() is None:
            return self._request_started("hello", {})
        resolved = shutil.which(self.binary) if not Path(self.binary).is_file() else self.binary
        if not resolved:
            raise RustKernelError(f"Rust code kernel not found: {self.binary}")
        self.process = subprocess.Popen(
            [resolved], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        self.reader = Thread(target=self._read, name="lighthouse-rust-kernel", daemon=True)
        Thread(target=self._read_stderr, name="lighthouse-rust-kernel-stderr", daemon=True).start()
        self.reader.start()
        return self._request_started("hello", {})

    def _read(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = message.get("id")
            if request_id is not None:
                with self.pending_lock:
                    item = self.pending.get(str(request_id))
                if item:
                    ready, box = item
                    box.update(message)
                    ready.set()
            elif message.get("method"):
                self.events.put(message)

    def _read_stderr(self) -> None:
        assert self.process and self.process.stderr
        for line in self.process.stderr:
            self.stderr.append(line.rstrip())

    def request(self, method: str, params: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        if not self.process or self.process.poll() is not None or not self.process.stdin:
            self.start()
        return self._request_started(method, params, timeout=timeout)

    def _request_started(self, method: str, params: dict[str, Any], *, timeout: float = 120.0) -> dict[str, Any]:
        assert self.process and self.process.poll() is None and self.process.stdin
        request_id = str(uuid4())
        ready = Event()
        box: dict[str, Any] = {}
        with self.pending_lock:
            self.pending[request_id] = (ready, box)
        with self.write_lock:
            self.process.stdin.write(json.dumps({"id": request_id, "method": method, "params": params}, separators=(",", ":")) + "\n")
            self.process.stdin.flush()
        if not ready.wait(timeout):
            with self.pending_lock:
                self.pending.pop(request_id, None)
            raise RustKernelError(f"Rust kernel request timed out: {method}")
        with self.pending_lock:
            self.pending.pop(request_id, None)
        if isinstance(box.get("error"), dict):
            raise RustKernelError(str(box["error"].get("message") or "Rust kernel request failed"))
        return dict(box.get("result") or {})

    def spawn(
        self,
        command: list[str],
        *,
        cwd: str,
        sandbox_mode: str = "workspaceWrite",
        writable_roots: list[str] | None = None,
        network_access: bool = False,
        timeout_ms: int = 600_000,
        pty: bool = True,
    ) -> dict[str, Any]:
        return self.request(
            "process/spawn",
            {
                "command": command,
                "cwd": cwd,
                "sandboxPolicy": sandbox_mode,
                "writableRoots": writable_roots or [cwd],
                "networkAccess": network_access,
                "timeoutMs": timeout_ms,
                "pty": pty,
            },
        )

    def write(self, process_id: str, data: str, *, close: bool = False) -> dict[str, Any]:
        return self.request("process/write", {"processId": process_id, "data": data, "close": close})

    def resize(self, process_id: str, rows: int, cols: int) -> dict[str, Any]:
        return self.request("process/resize", {"processId": process_id, "rows": rows, "cols": cols})

    def terminate(self, process_id: str) -> dict[str, Any]:
        return self.request("process/terminate", {"processId": process_id})

    def list_processes(self) -> dict[str, Any]:
        return self.request("process/list", {})

    def next_event(self, timeout: float | None = None) -> dict[str, Any] | None:
        try:
            return self.events.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        process = self.process
        self.process = None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
