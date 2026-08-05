from __future__ import annotations

import shlex
import time
from typing import Any

from ..codex_engine.rust_kernel import RustCodeKernelClient, RustKernelError
from ..models import Capability, ExecutionResult, Target
from .system import SystemExecutor


class RustBackedSystemExecutor:
    """Use the native PTY/sandbox sidecar for local shell and test processes.

    File, Git, service and SSH operations continue through the proven Python
    SystemExecutor. The Operation Kernel remains the sole caller, so normal
    confirmation, idempotency and immutable Receipt semantics are preserved.
    """

    _NATIVE_OPERATIONS = frozenset({"shell_exec", "test_run"})

    def __init__(
        self,
        *,
        binary: str = "lighthouse-code-kernel",
        fallback: SystemExecutor | None = None,
        client: RustCodeKernelClient | None = None,
    ) -> None:
        self.fallback = fallback or SystemExecutor()
        self.client = client or RustCodeKernelClient(binary)

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        transport = str(target.config.get("transport") or "local").lower()
        if capability.operation not in self._NATIVE_OPERATIONS or transport != "local":
            return self.fallback.execute(capability, target, arguments)
        try:
            return self._native(capability, target, arguments)
        except RustKernelError as exc:
            # Installation/configuration errors may fall back, but a native
            # process failure is returned by the sidecar as a normal exit event.
            value = self.fallback.execute(capability, target, arguments)
            value.result["native_kernel_fallback"] = {
                "used": True,
                "reason": str(exc),
            }
            return value

    def _native(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        command = str(arguments.get("command") or "").strip()
        if capability.operation == "test_run" and not command:
            command = str(target.config.get("test_command") or "").strip()
        if not command or "\x00" in command:
            raise ValueError("command is required")
        cwd = self.fallback._cwd(target, arguments)
        timeout = self.fallback._timeout(target, arguments)
        shell = str(target.config.get("shell") or "/bin/bash")
        argv = [shell, "-lc", f"cd -- {shlex.quote(cwd)} && {command}"]
        started = time.monotonic()
        spawned = self.client.spawn(
            argv,
            cwd=cwd,
            sandbox_mode="workspaceWrite",
            writable_roots=list(target.config.get("allowed_roots") or [cwd]),
            network_access=False,
            timeout_ms=timeout * 1000,
            pty=True,
        )
        process_id = str(spawned["processId"])
        chunks: list[str] = []
        exit_code: int | None = None
        status = "unknown"
        deadline = time.monotonic() + timeout + 5
        while time.monotonic() < deadline:
            event = self.client.next_event(timeout=0.25)
            if not event:
                continue
            params = event.get("params") if isinstance(event.get("params"), dict) else {}
            if str(params.get("processId") or "") != process_id:
                continue
            if event.get("method") == "process/output":
                chunks.append(str(params.get("data") or ""))
            elif event.get("method") == "process/exited":
                status = str(params.get("status") or "exited")
                value = params.get("exitCode")
                exit_code = int(value) if isinstance(value, int) else 124 if status == "timedOut" else 1
                break
        if exit_code is None:
            self.client.terminate(process_id)
            exit_code = 124
            status = "timedOut"
        output, truncated = self.fallback._truncate(
            "".join(chunks),
            self.fallback._max_output(target),
        )
        return ExecutionResult(
            ok=exit_code == 0,
            result={
                "transport": "local-rust-pty",
                "cwd": cwd,
                "process_id": process_id,
                "status": status,
                "exit_code": exit_code,
                "stdout": output,
                "stderr": "",
                "stdout_truncated": truncated,
                "stderr_truncated": False,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
            exit_code=exit_code,
        )
