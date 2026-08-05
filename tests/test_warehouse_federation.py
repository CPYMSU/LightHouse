from __future__ import annotations

from uuid import uuid4

import pytest

from lighthouse.capabilities import CapabilityRegistry, DEFAULT_CAPABILITIES
from lighthouse.kernel import OperationKernel
from lighthouse.models import ExecutionResult, KernelMode, OperationRequest, TargetKind
from lighthouse.repository import InMemoryRepository
from lighthouse.warehouse_federation import (
    WarehouseFederationError,
    normalize_origin,
    warehouse_instance_uuid,
)
from lighthouse.warehouse_federation_protocol import (
    PROTOCOL,
    WarehouseFederationProtocolError,
    make_envelope,
    parse_warehouse_message,
    project_agent_step,
)


class _Executor:
    def execute(self, capability, target, arguments):
        return ExecutionResult(ok=True, result={"capability": capability.tool_name})


def _kernel(run_id: str) -> tuple[OperationKernel, str]:
    repository = InMemoryRepository()
    target = repository.create_target(
        name="project",
        kind=TargetKind.SYSTEM,
        config={"allowed_roots": ["/tmp/project"]},
    )
    workspace = repository.create_workspace(
        name="project",
        data_target_id=None,
        system_target_id=target.id,
    )
    kernel = OperationKernel(
        repository,
        CapabilityRegistry(DEFAULT_CAPABILITIES),
        {"system": _Executor()},
        run_policy_resolver=lambda candidate: (
            {
                "mode": "read_only",
                "allow_local_write": False,
                "source": "warehouse_federation",
                "remote_run_id": str(uuid4()),
            }
            if candidate == run_id
            else None
        ),
    )
    return kernel, workspace.id


def test_kernel_blocks_remote_write_before_operation_creation() -> None:
    run_id = str(uuid4())
    kernel, workspace_id = _kernel(run_id)

    with pytest.raises(PermissionError, match="read-only policy"):
        kernel.submit(
            OperationRequest(
                capability="system.file.patch.v1",
                arguments={"patch": "diff --git a/a b/a"},
                workspace_id=workspace_id,
                actor="warehouse-device",
                mode=KernelMode.AUTO,
                idempotency_key=f"agent:{run_id}:1:abc",
            )
        )

    assert kernel.repository.operations == {}


def test_kernel_allows_read_and_binds_source_run_policy() -> None:
    run_id = str(uuid4())
    kernel, workspace_id = _kernel(run_id)

    result = kernel.submit(
        OperationRequest(
            capability="system.git.status.v1",
            arguments={},
            workspace_id=workspace_id,
            actor="warehouse-device",
            mode=KernelMode.AUTO,
            idempotency_key=f"agent:{run_id}:1:def",
        )
    )

    assert result["operation"]["status"] == "succeeded"
    envelope = kernel.repository.get_operation(result["operation"]["id"]).envelope
    assert envelope["source_run_id"] == run_id
    assert envelope["run_policy"]["mode"] == "read_only"
    assert envelope["run_policy"]["allow_local_write"] is False


def test_protocol_accepts_only_read_only_run_offers() -> None:
    raw = {
        "protocol": PROTOCOL,
        "message_id": str(uuid4()),
        "type": "run.offer",
        "sent_at": "2026-08-06T00:00:00Z",
        "payload": {
            "run_id": str(uuid4()),
            "goal": "Inspect the project and summarize failing tests",
            "policy": {"mode": "read_only", "allow_local_write": False},
        },
    }
    parsed = parse_warehouse_message(raw)
    assert parsed["payload"]["policy"] == {
        "mode": "read_only",
        "allow_local_write": False,
    }

    raw["message_id"] = str(uuid4())
    raw["payload"]["policy"] = {"mode": "write", "allow_local_write": True}
    with pytest.raises(WarehouseFederationProtocolError, match="read_only"):
        parse_warehouse_message(raw)


def test_protocol_rejects_remote_shell_invocation() -> None:
    with pytest.raises(WarehouseFederationProtocolError, match="Unsupported"):
        parse_warehouse_message(
            {
                "protocol": PROTOCOL,
                "message_id": str(uuid4()),
                "type": "shell.execute",
                "sent_at": "2026-08-06T00:00:00Z",
                "payload": {"command": "rm -rf /"},
            }
        )


def test_telemetry_projection_does_not_expose_arguments_or_secrets() -> None:
    projected = project_agent_step(
        {
            "sequence": 2,
            "kind": "decision",
            "payload": {
                "capability": "system.file.read.v1",
                "arguments": {"path": "/secret", "api_token": "never"},
                "reason": "contains private model context",
            },
            "created_at": "2026-08-06T00:00:00Z",
        }
    )
    assert projected["payload"] == {"capability": "system.file.read.v1"}

    envelope = make_envelope(
        "run.completed",
        {
            "run_id": str(uuid4()),
            "status": "completed",
            "result": {"message": "done", "api_token": "never"},
        },
    )
    assert envelope["payload"]["result"]["api_token"] == "[redacted]"


def test_origin_and_instance_identity_are_stable() -> None:
    assert normalize_origin("https://warehouse.example.com/") == (
        "https://warehouse.example.com"
    )
    assert warehouse_instance_uuid("default") == warehouse_instance_uuid("default")
    with pytest.raises(WarehouseFederationError, match="HTTPS"):
        normalize_origin("http://warehouse.example.com")
