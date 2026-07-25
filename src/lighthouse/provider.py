from __future__ import annotations

from dataclasses import dataclass
import json
import re
import time
from typing import Any, Callable, Protocol

import httpx


class ModelNotConfiguredError(RuntimeError):
    pass


class AgentProtocolError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentDecision:
    kind: str
    reason: str = ""
    capability: str | None = None
    arguments: dict[str, Any] | None = None
    message: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "capability": self.capability,
            "arguments": self.arguments,
            "message": self.message,
        }


class AgentProvider(Protocol):
    def decide(self, *, system_prompt: str, state: dict[str, Any]) -> AgentDecision: ...
    def distill(self, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]: ...


def parse_decision(value: Any) -> AgentDecision:
    if not isinstance(value, dict):
        raise AgentProtocolError("model decision must be a JSON object")
    kind = str(value.get("kind") or "").strip().lower()
    reason = str(value.get("reason") or "").strip()
    if kind == "tool":
        capability = str(value.get("capability") or "").strip()
        arguments = value.get("arguments")
        if not capability:
            raise AgentProtocolError("tool decision requires capability")
        if not isinstance(arguments, dict):
            raise AgentProtocolError("tool decision arguments must be an object")
        return AgentDecision(kind=kind, reason=reason, capability=capability, arguments=arguments)
    if kind in {"final", "ask"}:
        message = str(value.get("message") or "").strip()
        if not message:
            raise AgentProtocolError(f"{kind} decision requires message")
        return AgentDecision(kind=kind, reason=reason, message=message)
    raise AgentProtocolError("model decision kind must be tool, final or ask")


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _strip_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else stripped


class DisabledProvider:
    def decide(self, *, system_prompt: str, state: dict[str, Any]) -> AgentDecision:
        raise ModelNotConfiguredError(
            "AI model is not configured; set LIGHTHOUSE_MODEL, "
            "LIGHTHOUSE_MODEL_BASE_URL and LIGHTHOUSE_MODEL_API_KEY"
        )

    def distill(self, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise ModelNotConfiguredError("AI model is not configured for semantic distillation")


class OpenAICompatibleProvider:
    """Chat-Completions-compatible provider with bounded retry and usage receipts."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 120,
        json_mode: bool = True,
        max_state_chars: int = 120_000,
        client: httpx.Client | None = None,
        usage_recorder: Callable[[dict[str, Any]], Any] | None = None,
        transport_retries: int = 1,
    ):
        if not base_url or not model or not api_key:
            raise ModelNotConfiguredError("model base URL, model and API key are required")
        base = base_url.rstrip("/")
        self.endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        self.model = model
        self.provider_name = url_host(base_url)
        self.json_mode = bool(json_mode)
        self.max_state_chars = max(10_000, int(max_state_chars))
        self.usage_recorder = usage_recorder
        self.transport_retries = max(0, min(int(transport_retries), 3))
        self.client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
            },
        )

    def decide(self, *, system_prompt: str, state: dict[str, Any]) -> AgentDecision:
        state_text = self._bounded_json(state)
        run = state.get("run") if isinstance(state.get("run"), dict) else {}
        context = state.get("usage_context") if isinstance(state.get("usage_context"), dict) else {}
        usage_context = {
            **context,
            "workspace_id": context.get("workspace_id") or run.get("workspace_id"),
            "run_id": context.get("run_id") or run.get("id"),
            "call_kind": "main_ai",
        }
        value = self._json_completion(
            system_prompt=system_prompt,
            user_content=(
                "Continue the LightHouse run from this durable state. "
                "Return exactly one decision object.\n" + state_text
            ),
            usage_context=usage_context,
        )
        return parse_decision(value)

    def distill(self, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(kind or "memory").strip().lower()
        usage_context = (
            dict(payload.get("_usage_context") or {})
            if isinstance(payload.get("_usage_context"), dict)
            else {}
        )
        public_payload = {key: value for key, value in payload.items() if not str(key).startswith("_")}
        if kind == "specialist_work":
            role = str(public_payload.get("role") or "specialist")
            system_prompt = self._specialist_prompt(role)
            usage_context.setdefault("call_kind", f"agent:{role}")
        else:
            system_prompt = (
                "You are the hidden LightHouse Memory Steward. Distill durable context without "
                "issuing commands or making irreversible decisions. Preserve user intent, task "
                "progress, named entities, file or resource relationships, corrections, unresolved "
                "questions and evidence. Return one JSON object with summary, entities, relations, "
                "inferences and uncertainties. Each inference must include claim, confidence and "
                "based_on. Each uncertainty should include question, severity and evidence."
            )
            usage_context.setdefault("call_kind", "memory_distillation")
        value = self._json_completion(
            system_prompt=system_prompt,
            user_content=f"Distillation kind: {kind}\n" + self._bounded_json(public_payload),
            usage_context=usage_context,
        )
        if not isinstance(value, dict):
            raise AgentProtocolError("distillation response must be a JSON object")
        return value

    @staticmethod
    def _specialist_prompt(role: str) -> str:
        profiles = {
            "research": (
                "Investigate current mature approaches, source evidence, implementation patterns, "
                "tradeoffs and uncertainty. Prefer primary or authoritative sources when available."
            ),
            "taste": (
                "Exercise strong but context-sensitive visual judgment. Review hierarchy, grid, "
                "spacing, typography, information density, color proportion, originality and generic AI-template patterns."
            ),
            "frontend": (
                "Design or implement real frontend structure, interactions, accessibility and tests. "
                "Never describe mock values as live data."
            ),
            "backend": (
                "Trace and implement backend contracts, services, repositories, transactions, errors and tests."
            ),
            "wiring-verification": (
                "Verify every claimed feature across UI, event handler, API, service, repository, database, Receipt and E2E evidence."
            ),
            "integration": (
                "Integrate Build Cells conservatively, identify contract or merge conflicts, run focused integration checks and report evidence."
            ),
            "test-design": (
                "Design regression coverage from changed behavior, historical risks, boundaries, platforms and failure recovery."
            ),
            "contract": (
                "Extract or design explicit versioned API, data, event, capability and UI contracts with consumers and compatibility risks."
            ),
        }
        profile = profiles.get(role, "Provide focused specialist analysis for the main AI.")
        return (
            f"You are a LightHouse {role} Agent. {profile} "
            "The main AI remains Project Director and may wait, continue, or ignore your result. "
            "Return one JSON object with summary, findings, recommendations, risks, uncertainties, "
            "evidence, progress (0..1), criticality (background|checkpoint|important|critical), "
            "complete (boolean), and optional tool_calls. Each tool_call is an object with capability "
            "and arguments. Request tools only from allowed_tools in the supplied payload. Never claim "
            "a tool ran until its Receipt appears in tool_results. Keep output compact enough to distill."
        )

    def _bounded_json(self, value: Any) -> str:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        if len(text) <= self.max_state_chars:
            return text
        budget = self.max_state_chars
        head = max(1000, budget // 3)
        tail = max(1000, budget - head)
        return json.dumps(
            {"context_truncated": True, "head": text[:head], "tail": text[-tail:]},
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _json_completion(
        self,
        *,
        system_prompt: str,
        user_content: str,
        usage_context: dict[str, Any] | None = None,
    ) -> Any:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_error: Exception | None = None
        response: httpx.Response | None = None
        for attempt in range(self.transport_retries + 1):
            try:
                response = self.client.post(self.endpoint, json=payload)
                if response.status_code >= 500 and attempt < self.transport_retries:
                    time.sleep(0.25 * (attempt + 1))
                    continue
                if response.is_error:
                    raise RuntimeError(
                        f"model request failed with HTTP {response.status_code}: "
                        f"{response.text[:4000]}"
                    )
                break
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_error = exc
                if attempt >= self.transport_retries:
                    raise
                time.sleep(0.25 * (attempt + 1))
        if response is None:
            raise last_error or RuntimeError("model request failed before a response was received")
        data = response.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentProtocolError("model response has no assistant message") from exc
        raw_text = _message_text(message)
        self._record_usage(
            data.get("usage") if isinstance(data, dict) else None,
            system_prompt=system_prompt,
            user_content=user_content,
            output_text=raw_text,
            usage_context=usage_context or {},
        )
        text = _strip_fence(raw_text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AgentProtocolError("model response is not valid JSON") from exc

    def _record_usage(
        self,
        raw_usage: Any,
        *,
        system_prompt: str,
        user_content: str,
        output_text: str,
        usage_context: dict[str, Any],
    ) -> None:
        if self.usage_recorder is None:
            return
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        details_in = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        details_out = usage.get("completion_tokens_details") or usage.get("output_tokens_details") or {}
        estimated = input_tokens is None or output_tokens is None
        if input_tokens is None:
            input_tokens = max(1, (len(system_prompt) + len(user_content) + 3) // 4)
        if output_tokens is None:
            output_tokens = max(1, (len(output_text) + 3) // 4)
        record = {
            **usage_context,
            "provider": self.provider_name,
            "model": self.model,
            "input_tokens": int(input_tokens or 0),
            "output_tokens": int(output_tokens or 0),
            "cached_input_tokens": int(details_in.get("cached_tokens") or 0),
            "reasoning_tokens": int(details_out.get("reasoning_tokens") or 0),
            "total_tokens": int(
                usage.get("total_tokens") or int(input_tokens or 0) + int(output_tokens or 0)
            ),
            "estimated": estimated,
            "metadata": {"endpoint_host": self.provider_name},
        }
        try:
            self.usage_recorder(record)
        except Exception:
            pass


def url_host(value: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(value).netloc or "openai-compatible"
    except Exception:
        return "openai-compatible"
