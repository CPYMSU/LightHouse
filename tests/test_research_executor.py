from __future__ import annotations

import socket

import httpx
import pytest

from lighthouse.capabilities import CapabilityRegistry
from lighthouse.executors.research import ResearchExecutor, _public_url
from lighthouse.models import Target, TargetKind
from lighthouse.research_capabilities import RESEARCH_CAPABILITIES


def _target():
    return Target(
        id="system-target",
        name="system",
        kind=TargetKind.SYSTEM,
        config={"default_cwd": "/tmp", "allowed_roots": ["/tmp"]},
    )


def test_private_research_destination_is_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(ValueError, match="private or local"):
        _public_url("http://example.test/private")


def test_redirect_to_private_destination_is_rejected_before_following(monkeypatch):
    def resolve(host, port):
        address = "127.0.0.1" if host == "internal.test" else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(
            302,
            request=request,
            headers={"location": "http://internal.test/secret"},
        )

    executor = ResearchExecutor(httpx.Client(transport=httpx.MockTransport(handler)))
    capability = CapabilityRegistry(RESEARCH_CAPABILITIES).get("research.web.open.v1")
    with pytest.raises(ValueError, match="private or local"):
        executor.execute(capability, _target(), {"url": "https://public.test/page"})
    assert calls == ["https://public.test/page"]


def test_public_page_text_is_bounded_and_scripts_are_removed(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, port: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port or 443))],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><style>hidden</style><script>bad()</script><body><h1>Swiss Grid</h1><p>Evidence.</p></body></html>",
        )

    executor = ResearchExecutor(httpx.Client(transport=httpx.MockTransport(handler)))
    capability = CapabilityRegistry(RESEARCH_CAPABILITIES).get("research.web.open.v1")
    result = executor.execute(capability, _target(), {"url": "https://public.test/page"})
    assert result.ok is True
    assert "Swiss Grid" in result.result["text"]
    assert "bad()" not in result.result["text"]
    assert result.result["research_only"] is True
