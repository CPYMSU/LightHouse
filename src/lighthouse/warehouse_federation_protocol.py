from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Any, Mapping
from uuid import UUID, uuid4

PROTOCOL = "warehouse-lighthouse-federation/v1"
MAX_MESSAGE_BYTES = 256 * 1024
MAX_TEXT_CHARS = 16_384

WAREHOUSE_MESSAGE_TYPES = frozenset(
    {
        "run.offer",
        "run.input",
        "run.cancel",
        "message.ack",
        "operation.approval_granted",
        "operation.approval_denied",
    }
)
LIGHTHOUSE_MESSAGE_TYPES = frozenset(
    {
        "instance.hello",
        "instance.heartbeat",
        "run.accepted",
        "run.rejected",
        "run.event",
        "operation.approval_required",
        "receipt.committed",
        "run.completed",
        "message.ack",
    }
)

_SENSITIVE_PARTS = (
    "password",
    "secret",
    "token",
    "api_key",
    "credential",
    "passkey",
    "cookie",
    "authorization",
    "private_key",
)
_SAFE_STEP_FIELDS = frozenset(
    {
        "step",
        "capability",
        "status",
        "operation_id",
        "envelope_hash",
        "result_hash",
        "error_type",
        "role",
        "work_order_id",
        "progress",
        "message",
        "reason",
        "source",
        "workspace_id",
        "conversation_id",
        "deferred",
        "work_intensity",
    }
)
_USER_FACING_STEP_KINDS = frozenset(
    {
        "run_completed",
        "run_warning",
        "run_failed",
        "input_required",
        "provider_error",
        "protocol_error",
        "tool_rejected",
        "operation_dispatched",
        "observation",
    }
)


class WarehouseFederationProtocolError(ValueError):
    pass


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def payload_digest(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _uuid_text(value: object, field: str) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise WarehouseFederationProtocolError(f"{field} must be a UUID") from exc


def _text(value: object, field: str, *, maximum: int = MAX_TEXT_CHARS) -> str:
    item = str(value or "").strip()
    if not item:
        raise WarehouseFederationProtocolError(f"{field} is required")
    if len(item) > maximum:
        raise WarehouseFederationProtocolError(f"{field} is too long")
    return item


def _optional_text(value: object, field: str, *, maximum: int) -> str | None:
    if value in (None, ""):
        return None
    return _text(value, field, maximum=maximum)


def _sent_at(value: object) -> str:
    text = _text(value, "sent_at", maximum=64)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WarehouseFederationProtocolError("sent_at must be RFC3339") from exc
    return text


def redact(value: object, *, key: str = "", depth: int = 0) -> object:
    """Return a bounded projection suitable for federation telemetry."""
    if depth > 8:
        return "[truncated]"
    normalized = key.lower()
    if any(part in normalized for part in _SENSITIVE_PARTS):
        return "[redacted]"
    if isinstance(value, Mapping):
        return {
            str(child_key): redact(child_value, key=str(child_key), depth=depth + 1)
            for child_key, child_value in list(value.items())[:100]
        }
    if isinstance(value, (list, tuple)):
        return [redact(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return value if len(value) <= 8_000 else value[:8_000] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:8_000]


def make_envelope(
    message_type: str,
    payload: Mapping[str, object],
    *,
    message_id: UUID | str | None = None,
    sent_at: str | None = None,
) -> dict[str, object]:
    if message_type not in LIGHTHOUSE_MESSAGE_TYPES:
        raise WarehouseFederationProtocolError(
            f"Unsupported Lighthouse message type: {message_type}"
        )
    normalized_id = _uuid_text(message_id or uuid4(), "message_id")
    envelope = {
        "protocol": PROTOCOL,
        "message_id": normalized_id,
        "type": message_type,
        "sent_at": sent_at or utc_now_text(),
        "payload": redact(dict(payload)),
    }
    if len(canonical_json(envelope).encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise WarehouseFederationProtocolError("Federation message is too large")
    return envelope


def parse_warehouse_message(raw: object) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise WarehouseFederationProtocolError("Federation message must be an object")
    if raw.get("protocol") != PROTOCOL:
        raise WarehouseFederationProtocolError("Unsupported federation protocol")
    message_type = _text(raw.get("type"), "type", maximum=80)
    if message_type not in WAREHOUSE_MESSAGE_TYPES:
        raise WarehouseFederationProtocolError(
            f"Unsupported Warehouse message type: {message_type}"
        )
    payload = raw.get("payload")
    if not isinstance(payload, Mapping):
        raise WarehouseFederationProtocolError("payload must be an object")
    normalized: dict[str, object] = dict(payload)

    if message_type == "run.offer":
        normalized = {
            "run_id": _uuid_text(payload.get("run_id"), "payload.run_id"),
            "goal": _text(payload.get("goal"), "payload.goal"),
            "conversation_ref": _optional_text(
                payload.get("conversation_ref"),
                "payload.conversation_ref",
                maximum=128,
            ),
            "workspace_ref": _optional_text(
                payload.get("workspace_ref"),
                "payload.workspace_ref",
                maximum=256,
            ),
            "policy": _read_only_policy(payload.get("policy")),
        }
    elif message_type == "run.input":
        normalized = {
            "run_id": _uuid_text(payload.get("run_id"), "payload.run_id"),
            "text": _text(payload.get("text"), "payload.text"),
        }
    elif message_type == "run.cancel":
        normalized = {
            "run_id": _uuid_text(payload.get("run_id"), "payload.run_id"),
            "reason": _optional_text(
                payload.get("reason"), "payload.reason", maximum=500
            )
            or "Cancelled by Warehouse user",
        }
    elif message_type == "message.ack":
        acknowledged = payload.get("message_id", payload.get("received_message_id"))
        normalized = {"message_id": _uuid_text(acknowledged, "payload.message_id")}
    elif message_type.startswith("operation.approval_"):
        normalized = {
            "run_id": _uuid_text(payload.get("run_id"), "payload.run_id"),
            "operation_digest": _text(
                payload.get("operation_digest"),
                "payload.operation_digest",
                maximum=64,
            ),
        }
        if len(normalized["operation_digest"]) != 64 or any(
            character not in "0123456789abcdef"
            for character in str(normalized["operation_digest"]).lower()
        ):
            raise WarehouseFederationProtocolError(
                "payload.operation_digest must be a SHA-256 digest"
            )

    envelope = {
        "protocol": PROTOCOL,
        "message_id": _uuid_text(raw.get("message_id"), "message_id"),
        "type": message_type,
        "sent_at": _sent_at(raw.get("sent_at")),
        "payload": normalized,
    }
    if len(canonical_json(envelope).encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise WarehouseFederationProtocolError("Federation message is too large")
    return envelope


def _read_only_policy(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise WarehouseFederationProtocolError("payload.policy must be an object")
    mode = str(value.get("mode") or "").strip().lower()
    allow_local_write = value.get("allow_local_write")
    if mode != "read_only" or allow_local_write is not False:
        raise WarehouseFederationProtocolError(
            "Federation v1 accepts only read_only runs with local writes disabled"
        )
    return {"mode": "read_only", "allow_local_write": False}


def project_agent_step(step: Mapping[str, object]) -> dict[str, object]:
    kind = str(step.get("kind") or "activity")[:120]
    raw_payload = step.get("payload")
    payload = raw_payload if isinstance(raw_payload, Mapping) else {}
    projected: dict[str, object] = {}
    for field in _SAFE_STEP_FIELDS:
        if field not in payload:
            continue
        if field in {"message", "reason"} and kind not in _USER_FACING_STEP_KINDS:
            continue
        projected[field] = redact(payload[field], key=field)
    return {
        "sequence": int(step.get("sequence") or 0),
        "kind": kind,
        "status": _step_status(kind),
        "payload": projected,
        "created_at": str(step.get("created_at") or ""),
    }


def _step_status(kind: str) -> str:
    if kind in {"run_completed"}:
        return "succeeded"
    if kind in {"run_failed", "provider_error", "protocol_error"}:
        return "failed"
    if kind in {"input_required"}:
        return "waiting_input"
    if kind in {"tool_rejected"}:
        return "blocked"
    return "running"


def project_run_result(snapshot: Mapping[str, object]) -> dict[str, object]:
    run = snapshot.get("run")
    run = run if isinstance(run, Mapping) else {}
    return {
        "status": str(run.get("status") or "unknown"),
        "message": redact(run.get("final_message") or "", key="message"),
        "execution_status": str(run.get("execution_status") or "unknown"),
        "response_status": str(run.get("response_status") or "unknown"),
        "goal_status": str(run.get("goal_status") or "unknown"),
        "warning": redact(run.get("warning"), key="warning"),
        "updated_at": str(run.get("updated_at") or ""),
    }
