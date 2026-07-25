from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Protocol

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
    """Small provider adapter for Chat-Completions-compatible model APIs."""

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
    ):
        if not base_url or not model or not api_key:
            raise ModelNotConfiguredError("model base URL, model and API key are required")
        base = base_url.rstrip("/")
        self.endpoint = base if base.endswith("/chat/completions") else base + "/chat/completions"
        self.model = model
        self.json_mode = bool(json_mode)
        self.max_state_chars = max(10_000, int(max_state_chars))
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
        value = self._json_completion(
            system_prompt=system_prompt,
            user_content=(
                "Continue the LightHouse run from this durable state. "
                "Return exactly one decision object.\n" + state_text
            ),
        )
        return parse_decision(value)

    def distill(self, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        kind = str(kind or "memory").strip().lower()
        if kind == "specialist_work":
            system_prompt = (
                "You are a hidden LightHouse specialist agent. Analyze only the supplied "
                "work order and context. Do not claim to have executed tools or changed the "
                "world. Return one JSON object with summary, findings, recommendations, "
                "risks, uncertainties and evidence. Keep it compact and useful to the main AI."
            )
        else:
            system_prompt = (
                "You are the hidden LightHouse Memory Steward. Distill durable context without "
                "issuing commands or making irreversible decisions. Preserve user intent, task "
                "progress, named entities, file or resource relationships, corrections, unresolved "
                "questions and evidence. Return one JSON object with summary, entities, relations, "
                "inferences and uncertainties. Each inference must include claim, confidence and "
                "based_on. Each uncertainty should include question, severity and evidence."
            )
        value = self._json_completion(
            system_prompt=system_prompt,
            user_content=f"Distillation kind: {kind}\n" + self._bounded_json(payload),
        )
        if not isinstance(value, dict):
            raise AgentProtocolError("distillation response must be a JSON object")
        return value

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
            {
                "context_truncated": True,
                "head": text[:head],
                "tail": text[-tail:],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _json_completion(self, *, system_prompt: str, user_content: str) -> Any:
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
        response = self.client.post(self.endpoint, json=payload)
        if response.is_error:
            raise RuntimeError(
                f"model request failed with HTTP {response.status_code}: "
                f"{response.text[:4000]}"
            )
        data = response.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentProtocolError("model response has no assistant message") from exc
        text = _strip_fence(_message_text(message))
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AgentProtocolError("model response is not valid JSON") from exc
