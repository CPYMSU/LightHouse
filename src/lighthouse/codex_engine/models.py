from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class CodeEngineMode(StrEnum):
    """Authoritative coding-loop selection."""

    NATIVE = "native"
    CODEX = "codex"
    HYBRID = "hybrid"
    SHADOW = "shadow"
    AUTO = "auto"


class SandboxMode(StrEnum):
    READ_ONLY = "read-only"
    WORKSPACE_WRITE = "workspace-write"
    DANGER_FULL_ACCESS = "danger-full-access"
    EXTERNAL = "external"


class ApprovalPolicy(StrEnum):
    UNTRUSTED = "untrusted"
    ON_REQUEST = "on-request"
    UNLESS_TRUSTED = "unless-trusted"
    NEVER = "never"


class ApprovalDecision(StrEnum):
    ACCEPT = "accept"
    ACCEPT_FOR_SESSION = "acceptForSession"
    DECLINE = "decline"
    CANCEL = "cancel"


def normalize_engine_mode(value: Any, *, default: str = "auto") -> str:
    mode = str(value or default).strip().lower()
    aliases = {
        "on": "native",
        "off": "native",
        "compat": "codex",
        "compatibility": "codex",
        "compare": "shadow",
    }
    mode = aliases.get(mode, mode)
    allowed = {item.value for item in CodeEngineMode}
    if mode not in allowed:
        raise ValueError("code engine mode must be native, codex, hybrid, shadow, or auto")
    return mode


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class EnginePolicy:
    sandbox: SandboxMode = SandboxMode.WORKSPACE_WRITE
    approval: ApprovalPolicy = ApprovalPolicy.ON_REQUEST
    writable_roots: tuple[str, ...] = ()
    network_access: bool = False
    permissions_profile: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "writable_roots", tuple(self.writable_roots))
        if self.sandbox is SandboxMode.READ_ONLY and self.writable_roots:
            raise ValueError("read-only policy cannot declare writable roots")
        if self.permissions_profile and self.permissions_profile not in {
            ":read-only", ":workspace", ":danger-full-access"
        }:
            raise ValueError("unsupported built-in permission profile")

    def thread_params(self) -> dict[str, Any]:
        if self.permissions_profile:
            return {"permissions": self.permissions_profile}
        if self.sandbox is SandboxMode.READ_ONLY:
            sandbox: dict[str, Any] = {"type": "readOnly"}
        elif self.sandbox is SandboxMode.WORKSPACE_WRITE:
            sandbox = {
                "type": "workspaceWrite",
                "writableRoots": list(self.writable_roots),
                "networkAccess": self.network_access,
            }
        elif self.sandbox is SandboxMode.EXTERNAL:
            sandbox = {
                "type": "externalSandbox",
                "networkAccess": "enabled" if self.network_access else "restricted",
            }
        else:
            sandbox = {"type": "dangerFullAccess"}
        approval_map = {
            ApprovalPolicy.ON_REQUEST: "on-request",
            ApprovalPolicy.UNTRUSTED: "untrusted",
            ApprovalPolicy.UNLESS_TRUSTED: "unlessTrusted",
            ApprovalPolicy.NEVER: "never",
        }
        return {"sandboxPolicy": sandbox, "approvalPolicy": approval_map[self.approval]}


@dataclass(frozen=True)
class EngineEvent:
    method: str
    params: dict[str, Any]
    received_at: str = field(default_factory=_iso_now)

    def public_dict(self) -> dict[str, Any]:
        return {"method": self.method, "params": dict(self.params), "received_at": self.received_at}


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: int | str
    method: str
    params: dict[str, Any]

    @property
    def thread_id(self) -> str | None:
        value = self.params.get("threadId")
        return str(value) if value else None

    @property
    def turn_id(self) -> str | None:
        value = self.params.get("turnId")
        return str(value) if value else None

    def public_dict(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "method": self.method, "params": dict(self.params)}


@dataclass(frozen=True)
class ThreadBinding:
    thread_id: str
    cwd: str
    source: str = "codex-app-server-v2"
    created_at: str = field(default_factory=_iso_now)


@dataclass(frozen=True)
class TurnOutcome:
    status: str
    thread_id: str
    turn_id: str | None
    message: str
    events: tuple[EngineEvent, ...] = ()
    usage: dict[str, Any] = field(default_factory=dict)
    changed_paths: tuple[str, ...] = ()
    receipt_digest: str = ""
    approval: ApprovalRequest | None = None
    error: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        object.__setattr__(self, "usage", dict(self.usage))
        object.__setattr__(self, "changed_paths", tuple(self.changed_paths))

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "failed", "interrupted", "cancelled"}

    def public_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "thread_id": self.thread_id,
            "turn_id": self.turn_id,
            "message": self.message,
            "events": [item.public_dict() for item in self.events],
            "usage": dict(self.usage),
            "changed_paths": list(self.changed_paths),
            "receipt_digest": self.receipt_digest,
            "approval": self.approval.public_dict() if self.approval else None,
            "error": self.error,
        }
