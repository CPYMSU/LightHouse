from __future__ import annotations

from html import unescape
from html.parser import HTMLParser
import ipaddress
import re
import socket
from typing import Any
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from ..models import Capability, ExecutionResult, Target


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def _plain(value: str) -> str:
    return _SPACE_RE.sub(" ", unescape(_TAG_RE.sub(" ", value or ""))).strip()


def _public_url(value: str) -> str:
    raw = str(value or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("research URL must be public HTTP or HTTPS")
    host = parsed.hostname.strip("[]")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, parsed.port)}
    except OSError as exc:
        raise ValueError(f"research host could not be resolved: {host}") from exc
    for address in addresses:
        ip = ipaddress.ip_address(address)
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise ValueError("research tool cannot access private or local network addresses")
    return raw


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored:
            text = data.strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return _SPACE_RE.sub(" ", " ".join(self.parts)).strip()


class ResearchExecutor:
    """Read-only public-web research with bounded responses and redirect SSRF protection."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=False,
            headers={
                "User-Agent": "LightHouse-Research/1.2 (+https://github.com/CPYMSU/LightHouse)",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            },
        )

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        if capability.operation == "web_search":
            return self._search(arguments)
        if capability.operation == "web_open":
            return self._open(arguments)
        raise ValueError(f"unsupported research operation: {capability.operation}")

    def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        current = _public_url(url)
        for _ in range(6):
            response = self.client.request(method, current, **kwargs)
            if response.status_code not in {301, 302, 303, 307, 308}:
                _public_url(str(response.url))
                return response
            location = str(response.headers.get("location") or "").strip()
            if not location:
                return response
            current = _public_url(urljoin(current, location))
            if response.status_code == 303:
                method = "GET"
                kwargs.pop("data", None)
                kwargs.pop("json", None)
        raise RuntimeError("research request exceeded the redirect limit")

    def _search(self, arguments: dict[str, Any]) -> ExecutionResult:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise ValueError("research query is required")
        limit = max(1, min(int(arguments.get("max_results") or 8), 20))
        response = self._request(
            "POST",
            "https://html.duckduckgo.com/html/",
            data={"q": query},
        )
        response.raise_for_status()
        html = response.text
        links = re.findall(
            r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        snippets = re.findall(
            r'<(?:a|div)[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</(?:a|div)>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        results: list[dict[str, Any]] = []
        for index, (href, title_html) in enumerate(links[:limit]):
            url = unescape(href)
            parsed = urlparse(url)
            if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
                encoded = parse_qs(parsed.query).get("uddg", [""])[0]
                if encoded:
                    url = unquote(encoded)
            try:
                url = _public_url(url)
            except ValueError:
                continue
            results.append(
                {
                    "title": _plain(title_html),
                    "url": url,
                    "snippet": _plain(snippets[index]) if index < len(snippets) else "",
                    "rank": len(results) + 1,
                }
            )
        return ExecutionResult(
            ok=True,
            result={
                "query": query,
                "results": results,
                "count": len(results),
                "source": "DuckDuckGo HTML",
                "research_only": True,
            },
        )

    def _open(self, arguments: dict[str, Any]) -> ExecutionResult:
        url = _public_url(str(arguments.get("url") or ""))
        max_bytes = max(4_096, min(int(arguments.get("max_bytes") or 120_000), 500_000))
        response = self._request("GET", url)
        response.raise_for_status()
        content_type = str(response.headers.get("content-type") or "").lower()
        raw = response.content[:max_bytes]
        encoding = response.encoding or "utf-8"
        text = raw.decode(encoding, errors="replace")
        if "html" in content_type or "xml" in content_type:
            parser = _TextExtractor()
            parser.feed(text)
            text = parser.text()
        else:
            text = _SPACE_RE.sub(" ", text).strip()
        return ExecutionResult(
            ok=True,
            result={
                "url": str(response.url),
                "status_code": response.status_code,
                "content_type": content_type,
                "text": text[:200_000],
                "truncated": len(response.content) > max_bytes or len(text) > 200_000,
                "research_only": True,
            },
        )
