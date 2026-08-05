"""Codex app-server v2 wire helpers.

This is a clean-room client implementation against the documented public JSONL
protocol. It contains no copied Codex runtime implementation.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import ApprovalRequest, EngineEvent

MAX_MESSAGE_BYTES = 4 * 1024 * 1024
APPROVAL_METHOD_SUFFIX = "/requestApproval"
APPROVAL_METHODS = frozenset(
    {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
        "item/permissions/requestApproval",
        "mcpServer/toolCall/requestApproval",
    }
)
SENSITIVE_PARTS = (
    "authorization", "api_key", "apikey", "cookie", "credential", "password",
    "secret", "token", "private_key", "access_key",
)


class CodexProtocolError(ValueError):
    pass


def _bounded(value: object) -> object:
    raw = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise CodexProtocolError("Codex app-server message exceeds 4 MiB")
    return value


def make_request(request_id: int, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if request_id < 0:
        raise CodexProtocolError("request id must be non-negative")
    if not method or "/" not in method and method != "initialize":
        raise CodexProtocolError("invalid app-server method")
    return _bounded({"method": method, "id": request_id, "params": dict(params or {})})  # type: ignore[return-value]


def make_notification(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    if not method:
        raise CodexProtocolError("notification method is required")
    return _bounded({"method": method, "params": dict(params or {})})  # type: ignore[return-value]


def make_response(request_id: int | str, result: dict[str, Any]) -> dict[str, Any]:
    return _bounded({"id": request_id, "result": result})  # type: ignore[return-value]


def parse_message(raw: str | bytes | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if isinstance(raw, str):
        if len(raw.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise CodexProtocolError("Codex app-server message exceeds 4 MiB")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CodexProtocolError("invalid JSON from Codex app-server") from exc
    else:
        value = raw
    if not isinstance(value, dict):
        raise CodexProtocolError("Codex message must be an object")
    if "method" not in value and "id" not in value:
        raise CodexProtocolError("Codex message is neither request, notification, nor response")
    if "method" in value and not isinstance(value.get("method"), str):
        raise CodexProtocolError("Codex method must be a string")
    if "params" in value and not isinstance(value.get("params"), dict):
        raise CodexProtocolError("Codex params must be an object")
    if "error" in value and not isinstance(value.get("error"), dict):
        raise CodexProtocolError("Codex error must be an object")
    _bounded(value)
    return value


def is_server_request(message: dict[str, Any]) -> bool:
    return "method" in message and "id" in message


def is_notification(message: dict[str, Any]) -> bool:
    return "method" in message and "id" not in message


def is_response(message: dict[str, Any]) -> bool:
    return "id" in message and "method" not in message


def approval_from_message(message: dict[str, Any]) -> ApprovalRequest | None:
    if not is_server_request(message):
        return None
    method = str(message.get("method") or "")
    if method not in APPROVAL_METHODS and not method.endswith(APPROVAL_METHOD_SUFFIX):
        return None
    return ApprovalRequest(
        request_id=message["id"],
        method=method,
        params=dict(message.get("params") or {}),
    )


def redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 16:
        return "[redacted: nesting limit]"
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, child in value.items():
            key = str(raw_key)[:200]
            lowered = key.lower()
            out[key] = "[redacted]" if any(part in lowered for part in SENSITIVE_PARTS) else redact(child, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact(item, depth=depth + 1) for item in value[:1000]]
    if isinstance(value, str):
        return value[:100_000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:10_000]


def event_from_notification(message: dict[str, Any]) -> EngineEvent:
    if not is_notification(message):
        raise CodexProtocolError("message is not a notification")
    params = redact(dict(message.get("params") or {}))
    assert isinstance(params, dict)
    return EngineEvent(method=str(message["method"]), params=params)


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        redact(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def changed_paths_from_event(event: EngineEvent) -> tuple[str, ...]:
    params = event.params
    candidates: list[str] = []
    item = params.get("item") if isinstance(params.get("item"), dict) else params
    if isinstance(item, dict):
        for key in ("path", "filePath", "relativePath"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                candidates.append(value.strip())
        changes = item.get("changes")
        if isinstance(changes, list):
            for change in changes:
                if isinstance(change, dict):
                    value = change.get("path") or change.get("filePath")
                    if isinstance(value, str) and value.strip():
                        candidates.append(value.strip())
    return tuple(dict.fromkeys(candidates))


def assistant_delta(event: EngineEvent) -> str:
    if event.method not in {"item/agentMessage/delta", "item/reasoning/textDelta"}:
        return ""
    for key in ("delta", "text"):
        value = event.params.get(key)
        if isinstance(value, str):
            return value
    return ""
