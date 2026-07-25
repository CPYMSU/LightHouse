from __future__ import annotations

import httpx

from lighthouse.provider import OpenAICompatibleProvider


def test_provider_retries_transport_failure_and_records_exact_usage():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            raise httpx.ReadError("unexpected EOF", request=request)
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"kind":"final","message":"done","reason":"ok"}'
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 18,
                    "total_tokens": 138,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "completion_tokens_details": {"reasoning_tokens": 4},
                },
            },
        )

    recorded = []
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://model.example/v1",
        api_key="secret",
        model="test-model",
        client=client,
        usage_recorder=recorded.append,
        transport_retries=1,
    )
    decision = provider.decide(
        system_prompt="Decide.",
        state={
            "run": {"id": "run-1", "workspace_id": "workspace-1"},
            "usage_context": {"conversation_id": "conversation-1"},
        },
    )
    assert decision.kind == "final"
    assert calls["count"] == 2
    assert len(recorded) == 1
    assert recorded[0]["input_tokens"] == 120
    assert recorded[0]["output_tokens"] == 18
    assert recorded[0]["cached_input_tokens"] == 40
    assert recorded[0]["reasoning_tokens"] == 4
    assert recorded[0]["total_tokens"] == 138
    assert recorded[0]["estimated"] is False
    assert recorded[0]["run_id"] == "run-1"
    assert recorded[0]["conversation_id"] == "conversation-1"


def test_provider_marks_locally_estimated_usage_when_provider_omits_usage():
    request = httpx.Request("POST", "https://model.example/v1/chat/completions")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda incoming: httpx.Response(
                200,
                request=incoming,
                json={
                    "choices": [
                        {
                            "message": {
                                "content": '{"summary":"ok","findings":[],"complete":true}'
                            }
                        }
                    ]
                },
            )
        )
    )
    recorded = []
    provider = OpenAICompatibleProvider(
        base_url="https://model.example/v1",
        api_key="secret",
        model="test-model",
        client=client,
        usage_recorder=recorded.append,
    )
    provider.distill(kind="specialist_work", payload={"role": "research", "goal": "study"})
    assert recorded[0]["estimated"] is True
    assert recorded[0]["total_tokens"] > 0
    assert recorded[0]["call_kind"] == "agent:research"
