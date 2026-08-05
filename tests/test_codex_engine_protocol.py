from __future__ import annotations

import json

import pytest

from lighthouse.codex_engine.models import EnginePolicy, SandboxMode
from lighthouse.codex_engine.protocol import (
    CodexProtocolError,
    approval_from_message,
    canonical_digest,
    make_request,
    parse_message,
    redact,
)


def test_codex_request_omits_jsonrpc_header() -> None:
    value = make_request(1, "thread/start", {"cwd": "/tmp/project"})
    assert value == {"id": 1, "method": "thread/start", "params": {"cwd": "/tmp/project"}}
    assert "jsonrpc" not in value


def test_approval_request_and_redaction() -> None:
    raw = {
        "id": 9,
        "method": "item/commandExecution/requestApproval",
        "params": {"threadId": "t", "command": ["echo", "ok"], "api_token": "secret"},
    }
    approval = approval_from_message(parse_message(raw))
    assert approval is not None
    assert approval.request_id == 9
    assert redact(raw)["params"]["api_token"] == "[redacted]"


def test_protocol_rejects_invalid_message() -> None:
    with pytest.raises(CodexProtocolError):
        parse_message(json.dumps([1, 2, 3]))


def test_policy_maps_to_codex_v2_shape() -> None:
    value = EnginePolicy(sandbox=SandboxMode.READ_ONLY).thread_params()
    assert value["sandboxPolicy"] == {"type": "readOnly"}
    assert len(canonical_digest(value)) == 64
