from __future__ import annotations

from threading import Event
import time

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.kernel import OperationKernel
from lighthouse.models import Capability, ConfirmationMode, ExecutionResult, KernelMode, OperationRequest, Risk, TargetKind
from lighthouse.repository import InMemoryRepository


class BlockingExecutor:
    def __init__(self):
        self.started = Event()
        self.release = Event()

    def execute(self, capability, target, arguments):
        self.started.set()
        assert self.release.wait(timeout=5)
        return ExecutionResult(ok=True, result={"cwd": arguments.get("cwd"), "done": True})


def test_confirm_deferred_returns_running_and_receipt_is_recoverable():
    repository = InMemoryRepository()
    target = repository.create_target(name="local", kind=TargetKind.SYSTEM, config={"transport": "local", "default_cwd": "/tmp", "allowed_roots": ["/tmp"]})
    workspace = repository.create_workspace(name="workspace", data_target_id=None, system_target_id=target.id)
    capability = Capability(tool_name="system.test.blocking.v1", command="blocking", description="blocking test operation", kernel=KernelMode.SYSTEM, executor="blocking", operation="blocking", risk=Risk.HIGH, confirmation=ConfirmationMode.EXPLICIT, writes=True)
    executor = BlockingExecutor()
    kernel = OperationKernel(repository, CapabilityRegistry((capability,)), {"blocking": executor})
    submitted = kernel.submit(OperationRequest(capability=capability.tool_name, arguments={"cwd": "/tmp"}, workspace_id=workspace.id, actor="operator"))
    operation_id = submitted["operation"]["id"]
    started = time.monotonic()
    running = kernel.confirm_deferred(operation_id, actor="operator")
    assert time.monotonic() - started < 0.5
    assert running["operation"]["status"] == "running"
    assert executor.started.wait(timeout=1)
    assert kernel.confirm_deferred(operation_id, actor="operator")["operation"]["status"] == "running"
    executor.release.set()
    deadline = time.monotonic() + 3
    final = kernel.snapshot(operation_id)
    while final["operation"]["status"] == "running" and time.monotonic() < deadline:
        time.sleep(0.02)
        final = kernel.snapshot(operation_id)
    assert final["operation"]["status"] == "succeeded"
    assert final["receipt"]["result"]["done"] is True
