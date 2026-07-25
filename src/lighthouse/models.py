from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
import base64
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class KernelMode(StrEnum):
    DATA = "data"
    SYSTEM = "system"
    DESKTOP = "desktop"
    AUTO = "auto"


class TargetKind(StrEnum):
    DATA = "data"
    SYSTEM = "system"
    DESKTOP = "desktop"


class Risk(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ConfirmationMode(StrEnum):
    DIRECT = "direct"
    EXPLICIT = "explicit"
    PASSKEY = "passkey"


class OperationStatus(StrEnum):
    CREATED = "created"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRunStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    WAITING_INPUT = "waiting_input"
    SUCCEEDED = "succeeded"
    COMPLETED_WITH_WARNING = "completed_with_warning"
    PARTIALLY_COMPLETED = "partially_completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (Decimal, UUID, Path)):
        return str(value)
    if isinstance(value, bytes):
        return {"encoding": "base64", "value": base64.b64encode(value).decode("ascii")}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    return str(value)


def canonical_json(value: Any) -> str:
    return json.dumps(
        json_safe(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Capability:
    tool_name: str
    command: str
    description: str
    kernel: KernelMode
    executor: str
    operation: str
    risk: Risk
    confirmation: ConfirmationMode
    writes: bool
    aliases: tuple[str, ...] = ()
    arguments: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "command": self.command,
            "description": self.description,
            "kernel": self.kernel.value,
            "risk": self.risk.value,
            "confirmation": self.confirmation.value,
            "writes": self.writes,
            "aliases": list(self.aliases),
            "arguments": self.arguments,
        }


@dataclass(frozen=True)
class Target:
    id: str
    name: str
    kind: TargetKind
    config: dict[str, Any]
    active: bool = True


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    data_target_id: str | None
    system_target_id: str | None
    desktop_target_id: str | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OperationRequest:
    capability: str
    arguments: dict[str, Any]
    workspace_id: str
    actor: str
    mode: KernelMode = KernelMode.AUTO
    idempotency_key: str | None = None
    operation_id: str = field(default_factory=lambda: str(uuid4()))

    def envelope(self, *, target_id: str, capability: Capability) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "workspace_id": self.workspace_id,
            "target_id": target_id,
            "actor": self.actor,
            "requested_mode": self.mode.value,
            "kernel": capability.kernel.value,
            "capability": capability.tool_name,
            "arguments": json_safe(self.arguments),
            "idempotency_key": self.idempotency_key,
        }


@dataclass(frozen=True)
class ExecutionResult:
    ok: bool
    result: dict[str, Any]
    exit_code: int | None = None


@dataclass(frozen=True)
class OperationView:
    id: str
    status: OperationStatus
    capability: str
    kernel: KernelMode
    target_id: str
    workspace_id: str
    actor: str
    envelope: dict[str, Any]
    envelope_hash: str
    request_hash: str
    created_at: datetime
    updated_at: datetime

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status.value,
            "capability": self.capability,
            "kernel": self.kernel.value,
            "target_id": self.target_id,
            "workspace_id": self.workspace_id,
            "actor": self.actor,
            "envelope": self.envelope,
            "envelope_hash": self.envelope_hash,
            "request_hash": self.request_hash,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


@dataclass(frozen=True)
class AgentRunView:
    id: str
    task: str
    workspace_id: str
    actor: str
    mode: KernelMode
    status: AgentRunStatus
    max_steps: int
    current_step: int
    auto_confirm: bool
    pending_operation_id: str | None
    final_message: str | None
    created_at: datetime
    updated_at: datetime
    execution_status: str = "not_started"
    response_status: str = "pending"
    goal_status: str = "unknown"
    warning: str | None = None
    auto_scope: dict[str, Any] = field(default_factory=dict)

    def public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task,
            "workspace_id": self.workspace_id,
            "actor": self.actor,
            "mode": self.mode.value,
            "status": self.status.value,
            "max_steps": self.max_steps,
            "current_step": self.current_step,
            "auto_confirm": self.auto_confirm,
            "auto_scope": self.auto_scope,
            "pending_operation_id": self.pending_operation_id,
            "final_message": self.final_message,
            "execution_status": self.execution_status,
            "response_status": self.response_status,
            "goal_status": self.goal_status,
            "warning": self.warning,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
