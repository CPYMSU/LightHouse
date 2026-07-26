from __future__ import annotations

import json
import time
from typing import Any

import httpx

from .agent_results import AgentBusStructuredProvider
from .cognitive import CognitiveAgentDecision, parse_cognitive_decision
from .provider import AgentProtocolError, _message_text, _strip_fence


_REASONING_EFFORT = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    # Chat-Completions-compatible providers do not agree on xhigh/max. Extreme
    # keeps its larger budgets and review policy while using the strongest
    # broadly compatible wire value; providers that reject it fall back safely.
    "max": "high",
}


def _effort_from_intensity(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    effective = value.get("effective") if isinstance(value.get("effective"), dict) else value
    requested = str(effective.get("reasoning_effort") or "").strip().lower()
    return _REASONING_EFFORT.get(requested)


class IntensityAwareAgentBusProvider(AgentBusStructuredProvider):
    """Use provider reasoning effort when supported, with transparent fallback."""

    def decide(self, *, system_prompt: str, state: dict[str, Any]) -> CognitiveAgentDecision:
        state_text = self._bounded_json(state)
        run = state.get("run") if isinstance(state.get("run"), dict) else {}
        context = state.get("usage_context") if isinstance(state.get("usage_context"), dict) else {}
        intensity = state.get("work_intensity") if isinstance(state.get("work_intensity"), dict) else {}
        usage_context = {
            **context,
            "workspace_id": context.get("workspace_id") or run.get("workspace_id"),
            "run_id": context.get("run_id") or run.get("id"),
            "call_kind": "main_ai",
            "reasoning_effort": _effort_from_intensity(intensity),
            "selected_intensity": intensity.get("selected"),
        }
        value = self._json_completion(
            system_prompt=system_prompt,
            user_content=(
                "Continue the LightHouse run from this durable state. "
                "Return exactly one decision object. Include display or cognitive_delta only "
                "when they add meaningful continuity for the user and your next turn.\n"
                + state_text
            ),
            usage_context=usage_context,
        )
        return parse_cognitive_decision(value)

    def distill(self, *, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        value = dict(payload or {})
        intensity = value.get("intensity") if isinstance(value.get("intensity"), dict) else {}
        usage = dict(value.get("_usage_context") or {}) if isinstance(value.get("_usage_context"), dict) else {}
        usage.setdefault("reasoning_effort", _effort_from_intensity(intensity))
        usage.setdefault("selected_intensity", intensity.get("selected"))
        value["_usage_context"] = usage
        return super().distill(kind=kind, payload=value)

    def _json_completion(
        self,
        *,
        system_prompt: str,
        user_content: str,
        usage_context: dict[str, Any] | None = None,
    ) -> Any:
        usage_context = dict(usage_context or {})
        reasoning_effort = str(usage_context.get("reasoning_effort") or "").strip().lower()
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
        if reasoning_effort in {"low", "medium", "high"}:
            payload["reasoning_effort"] = reasoning_effort

        last_error: Exception | None = None
        response: httpx.Response | None = None
        effort_fallback = False
        for attempt in range(self.transport_retries + 2):
            try:
                response = self.client.post(self.endpoint, json=payload)
                if (
                    response.status_code in {400, 404, 422}
                    and "reasoning_effort" in payload
                    and not effort_fallback
                ):
                    payload.pop("reasoning_effort", None)
                    effort_fallback = True
                    usage_context["reasoning_effort_fallback"] = True
                    continue
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
            usage_context=usage_context,
        )
        text = _strip_fence(raw_text)
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise AgentProtocolError("model response is not valid JSON") from exc
