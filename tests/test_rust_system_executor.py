from __future__ import annotations

from pathlib import Path

from lighthouse.executors.rust_system import RustBackedSystemExecutor
from lighthouse.models import Capability, ConfirmationMode, KernelMode, Risk, Target, TargetKind


class FakeClient:
    def __init__(self) -> None:
        self.events = [
            {"method": "process/output", "params": {"processId": "p1", "data": "ok\n"}},
            {"method": "process/exited", "params": {"processId": "p1", "status": "exited", "exitCode": 0}},
        ]

    def spawn(self, *_args, **_kwargs):
        return {"processId": "p1"}

    def next_event(self, timeout=None):
        return self.events.pop(0) if self.events else None

    def terminate(self, process_id):
        return {"processId": process_id, "terminated": True}


def test_rust_executor_preserves_operation_result(tmp_path: Path) -> None:
    target = Target(
        id="target",
        name="local",
        kind=TargetKind.SYSTEM,
        config={
            "transport": "local",
            "platform": "linux",
            "default_cwd": str(tmp_path),
            "allowed_roots": [str(tmp_path)],
            "shell": "/bin/bash",
            "timeout": 30,
            "max_output_chars": 10000,
        },
    )
    capability = Capability(
        tool_name="system.test.run.v1",
        command="test run",
        description="test",
        kernel=KernelMode.SYSTEM,
        executor="system",
        operation="test_run",
        risk=Risk.NORMAL,
        confirmation=ConfirmationMode.EXPLICIT,
        writes=True,
    )
    result = RustBackedSystemExecutor(client=FakeClient()).execute(
        capability,
        target,
        {"command": "printf ok", "cwd": str(tmp_path)},
    )
    assert result.ok is True
    assert result.result["transport"] == "local-rust-pty"
    assert result.result["stdout"] == "ok\n"
