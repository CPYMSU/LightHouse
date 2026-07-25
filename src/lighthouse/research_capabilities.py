from __future__ import annotations

from .models import Capability, ConfirmationMode, KernelMode, Risk


RESEARCH_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        tool_name="research.web.search.v1",
        command="research web search",
        description=(
            "Search the public web for current design, engineering, standards or implementation evidence. "
            "This is a read-only research primitive; the main AI decides whether and how to use its results."
        ),
        kernel=KernelMode.SYSTEM,
        executor="research",
        operation="web_search",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("web research", "search current designs", "研究成熟設計", "網頁研究"),
        arguments={
            "query": {"type": "string", "required": True},
            "max_results": {"type": "integer", "required": False},
        },
    ),
    Capability(
        tool_name="research.web.open.v1",
        command="research web open",
        description=(
            "Read bounded text from a public HTTP or HTTPS page for evidence-backed research. "
            "Private, loopback and link-local destinations are rejected."
        ),
        kernel=KernelMode.SYSTEM,
        executor="research",
        operation="web_open",
        risk=Risk.LOW,
        confirmation=ConfirmationMode.DIRECT,
        writes=False,
        aliases=("open research source", "read webpage", "閱讀研究來源"),
        arguments={
            "url": {"type": "string", "required": True},
            "max_bytes": {"type": "integer", "required": False},
        },
    ),
)
