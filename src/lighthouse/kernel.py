from __future__ import annotations

from typing import Any

from .capabilities import CapabilityRegistry
from .models import ConfirmationMode, ExecutionResult, KernelMode, OperationRequest, OperationStatus, TargetKind, json_safe
from .repository import Repository


class OperationKernel:
    def __init__(self, repository: Repository, registry: CapabilityRegistry, executors: dict[str, Any]):
        self.repository = repository
        self.registry = registry
        self.executors = dict(executors)

    def submit(self, request: OperationRequest) -> dict[str, Any]:
        capability = self.registry.get(request.capability)
        if request.mode not in {KernelMode.AUTO, capability.kernel}:
            raise ValueError(f"capability requires {capability.kernel.value} mode")
        workspace = self.repository.get_workspace(request.workspace_id)
        target_by_kernel = {
            KernelMode.DATA: workspace.data_target_id,
            KernelMode.SYSTEM: workspace.system_target_id,
            KernelMode.DESKTOP: workspace.desktop_target_id,
        }
        target_id = target_by_kernel.get(capability.kernel)
        if not target_id:
            raise ValueError(f"workspace has no {capability.kernel.value} target")
        target = self.repository.get_target(target_id)
        expected_kind = {
            KernelMode.DATA: TargetKind.DATA,
            KernelMode.SYSTEM: TargetKind.SYSTEM,
            KernelMode.DESKTOP: TargetKind.DESKTOP,
        }[capability.kernel]
        if target.kind != expected_kind:
            raise ValueError("workspace target kind does not match capability kernel")
        envelope = request.envelope(target_id=target.id, capability=capability)
        operation = self.repository.create_operation(operation_id=request.operation_id, workspace_id=workspace.id, target_id=target.id, capability=capability.tool_name, kernel=capability.kernel, actor=request.actor, envelope=envelope, idempotency_key=request.idempotency_key)
        if operation.id != request.operation_id:
            return self.snapshot(operation.id)
        self.repository.append_event(operation.id, "operation_created", {"capability": capability.tool_name, "kernel": capability.kernel.value, "target_id": target.id})
        if capability.confirmation == ConfirmationMode.DIRECT:
            return self._execute(operation.id, expected=OperationStatus.CREATED)
        operation = self.repository.set_operation_status(operation.id, OperationStatus.AWAITING_CONFIRMATION)
        self.repository.append_event(operation.id, "confirmation_required", {"mode": capability.confirmation.value, "envelope_hash": operation.envelope_hash})
        return self.snapshot(operation.id)

    def confirm(self, operation_id: str, *, actor: str) -> dict[str, Any]:
        operation = self.repository.get_operation(operation_id)
        if operation.actor != actor:
            raise PermissionError("only the operation actor may confirm it")
        if operation.status == OperationStatus.SUCCEEDED:
            return self.snapshot(operation_id)
        if operation.status != OperationStatus.AWAITING_CONFIRMATION:
            raise ValueError(f"operation cannot be confirmed from status {operation.status.value}")
        self.repository.append_event(operation_id, "operation_confirmed", {"actor": actor, "envelope_hash": operation.envelope_hash})
        return self._execute(operation_id, expected=OperationStatus.AWAITING_CONFIRMATION)

    def snapshot(self, operation_id: str) -> dict[str, Any]:
        operation = self.repository.get_operation(operation_id)
        return {"operation": operation.public_dict(), "events": self.repository.list_events(operation_id), "receipt": self.repository.get_receipt(operation_id)}

    def _execute(self, operation_id: str, *, expected: OperationStatus) -> dict[str, Any]:
        claimed = self.repository.claim_operation(operation_id, expected)
        if claimed is None:
            current = self.repository.get_operation(operation_id)
            if current.status == OperationStatus.SUCCEEDED:
                return self.snapshot(operation_id)
            raise RuntimeError(f"operation is already claimed or finished: {current.status.value}")
        capability = self.registry.get(claimed.capability)
        target = self.repository.get_target(claimed.target_id)
        executor = self.executors.get(capability.executor)
        if executor is None:
            raise RuntimeError(f"executor is not configured: {capability.executor}")
        self.repository.append_event(operation_id, "execution_started", {"executor": capability.executor})
        try:
            execution: ExecutionResult = executor.execute(capability, target, claimed.envelope.get("arguments") or {})
            payload = json_safe(execution.result)
            receipt = self.repository.save_receipt(operation_id, ok=execution.ok, result=payload)
            status = OperationStatus.SUCCEEDED if execution.ok else OperationStatus.FAILED
            self.repository.set_operation_status(operation_id, status)
            self.repository.append_event(operation_id, "execution_succeeded" if execution.ok else "execution_failed", {"result_hash": receipt["result_hash"], "exit_code": execution.exit_code})
        except Exception as exc:
            payload = {"error": str(exc), "error_type": type(exc).__name__}
            receipt = self.repository.save_receipt(operation_id, ok=False, result=payload)
            self.repository.set_operation_status(operation_id, OperationStatus.FAILED)
            self.repository.append_event(operation_id, "execution_failed", {"result_hash": receipt["result_hash"], **payload})
        return self.snapshot(operation_id)
