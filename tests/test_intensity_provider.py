from __future__ import annotations

from lighthouse.intensity_provider import IntensityAwareAgentBusProvider


class FakeResponse:
    def __init__(self, status_code: int, payload: dict, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def post(self, endpoint, json):
        self.payloads.append(dict(json))
        return self.responses.pop(0)


def _success(message: str = '{"kind":"final","message":"done","reason":"verified"}'):
    return FakeResponse(
        200,
        {
            "choices": [{"message": {"content": message}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
    )


def test_advanced_intensity_reaches_the_provider_as_high_reasoning_effort():
    client = FakeClient([_success()])
    provider = IntensityAwareAgentBusProvider(
        base_url="https://provider.example/v1",
        api_key="test-key",
        model="test-model",
        client=client,
    )

    decision = provider.decide(
        system_prompt="system",
        state={
            "run": {"id": "run-1", "workspace_id": "workspace-1"},
            "work_intensity": {
                "selected": "advanced",
                "effective": {"reasoning_effort": "high"},
            },
        },
    )

    assert decision.kind == "final"
    assert client.payloads[0]["reasoning_effort"] == "high"


def test_unsupported_reasoning_effort_falls_back_without_blocking_the_run():
    client = FakeClient(
        [
            FakeResponse(422, {"error": {"message": "unknown field reasoning_effort"}}, "unsupported"),
            _success(),
        ]
    )
    provider = IntensityAwareAgentBusProvider(
        base_url="https://provider.example/v1",
        api_key="test-key",
        model="test-model",
        client=client,
    )

    decision = provider.decide(
        system_prompt="system",
        state={
            "run": {"id": "run-2", "workspace_id": "workspace-1"},
            "work_intensity": {
                "selected": "extreme",
                "effective": {"reasoning_effort": "max"},
            },
        },
    )

    assert decision.message == "done"
    assert client.payloads[0]["reasoning_effort"] == "high"
    assert "reasoning_effort" not in client.payloads[1]
