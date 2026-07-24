from __future__ import annotations

import unittest

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.kernel import OperationKernel
from lighthouse.models import ExecutionResult, KernelMode, OperationRequest, OperationStatus, TargetKind
from lighthouse.repository import InMemoryRepository


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def execute(self, capability, target, arguments):
        self.calls.append((capability.tool_name, target.id, arguments))
        return ExecutionResult(ok=True, result={"tool": capability.tool_name, "arguments": arguments})


class KernelTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryRepository()
        data = self.repository.create_target(name="db", kind=TargetKind.DATA, config={"dsn_env": "TEST_DSN"})
        system = self.repository.create_target(name="server", kind=TargetKind.SYSTEM, config={"transport": "local"})
        self.workspace = self.repository.create_workspace(name="project", data_target_id=data.id, system_target_id=system.id)
        self.postgres = FakeExecutor()
        self.system = FakeExecutor()
        self.kernel = OperationKernel(self.repository, CapabilityRegistry(), {"postgres": self.postgres, "system": self.system})

    def request(self, capability, arguments=None, **kwargs):
        return OperationRequest(capability=capability, arguments=arguments or {}, workspace_id=self.workspace.id, actor="adsin", **kwargs)

    def test_direct_read_executes_and_receipts(self):
        result = self.kernel.submit(self.request("data.sql.query.v1", {"sql": "select 1"}, mode=KernelMode.DATA))
        self.assertEqual(result["operation"]["status"], OperationStatus.SUCCEEDED.value)
        self.assertTrue(result["receipt"]["ok"])
        self.assertEqual(len(self.postgres.calls), 1)

    def test_high_risk_waits_for_one_confirmation(self):
        pending = self.kernel.submit(self.request("system.service.restart.v1", {"service": "warehouse-api"}, mode=KernelMode.SYSTEM))
        operation_id = pending["operation"]["id"]
        self.assertEqual(pending["operation"]["status"], OperationStatus.AWAITING_CONFIRMATION.value)
        self.assertEqual(self.system.calls, [])
        done = self.kernel.confirm(operation_id, actor="adsin")
        self.assertEqual(done["operation"]["status"], OperationStatus.SUCCEEDED.value)
        self.assertEqual(len(self.system.calls), 1)
        replay = self.kernel.confirm(operation_id, actor="adsin")
        self.assertEqual(replay["operation"]["status"], OperationStatus.SUCCEEDED.value)
        self.assertEqual(len(self.system.calls), 1)

    def test_idempotency_replay_does_not_execute_twice(self):
        first = self.kernel.submit(self.request("data.sql.query.v1", {"sql": "select 1"}, idempotency_key="read-1"))
        second = self.kernel.submit(self.request("data.sql.query.v1", {"sql": "select 1"}, idempotency_key="read-1"))
        self.assertEqual(first["operation"]["id"], second["operation"]["id"])
        self.assertEqual(len(self.postgres.calls), 1)

    def test_idempotency_key_cannot_change_request(self):
        self.kernel.submit(self.request("data.sql.query.v1", {"sql": "select 1"}, idempotency_key="same"))
        with self.assertRaises(ValueError):
            self.kernel.submit(self.request("data.sql.query.v1", {"sql": "select 2"}, idempotency_key="same"))

    def test_mode_cannot_cross_kernel(self):
        with self.assertRaises(ValueError):
            self.kernel.submit(self.request("system.git.status.v1", {}, mode=KernelMode.DATA))

    def test_receipt_is_immutable(self):
        result = self.kernel.submit(self.request("data.sql.query.v1", {"sql": "select 1"}))
        operation_id = result["operation"]["id"]
        with self.assertRaises(ValueError):
            self.repository.save_receipt(operation_id, ok=True, result={"changed": True})


if __name__ == "__main__":
    unittest.main()
