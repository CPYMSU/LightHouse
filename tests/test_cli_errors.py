from __future__ import annotations

import httpx
import pytest

from lighthouse.cli import CLIError, Client, exception_message


def _client(handler) -> Client:
    client = Client("http://127.0.0.1:8787", "x" * 32)
    client.client.close()
    client.client = httpx.Client(
        base_url="http://127.0.0.1:8787",
        transport=httpx.MockTransport(handler),
        trust_env=False,
    )
    return client


def test_cli_error_never_renders_blank() -> None:
    assert "without a server message" in str(CLIError(""))
    assert "no diagnostic text" in exception_message(OSError())


def test_empty_http_error_is_actionable() -> None:
    client = _client(
        lambda request: httpx.Response(
            500,
            json={"detail": ""},
            request=request,
        )
    )
    with pytest.raises(CLIError, match=r"POST /v1/admin/migrate failed with HTTP 500"):
        client.request("POST", "/v1/admin/migrate", {})


def test_structured_http_error_is_preserved() -> None:
    client = _client(
        lambda request: httpx.Response(
            422,
            json={"detail": [{"loc": ["body"], "msg": "invalid"}]},
            request=request,
        )
    )
    with pytest.raises(CLIError, match="invalid"):
        client.request("POST", "/v1/targets", {})


def test_network_error_names_endpoint_and_exception() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    client = _client(fail)
    with pytest.raises(CLIError, match=r"cannot reach LightHouse at http://127\.0\.0\.1:8787"):
        client.request("GET", "/healthz")
