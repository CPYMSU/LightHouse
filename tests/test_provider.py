import pytest

from lighthouse.provider import AgentProtocolError, parse_decision


def test_parse_tool_decision_is_strict():
    decision = parse_decision(
        {
            "kind": "tool",
            "capability": "system.git.status.v1",
            "arguments": {},
            "reason": "inspect",
        }
    )
    assert decision.capability == "system.git.status.v1"


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"kind": "tool", "capability": "x", "arguments": []},
        {"kind": "final", "message": ""},
        {"kind": "unknown"},
    ],
)
def test_invalid_decisions_fail_closed(value):
    with pytest.raises(AgentProtocolError):
        parse_decision(value)


def test_openai_compatible_provider_parses_json_response():
    import httpx

    from lighthouse.provider import OpenAICompatibleProvider

    def handler(request):
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"kind":"final","message":"done","reason":"verified"}'
                            )
                        }
                    }
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://model.example/v1",
        api_key="secret",
        model="test",
        client=client,
    )
    decision = provider.decide(system_prompt="system", state={"task": "x"})
    assert decision.kind == "final"
    assert decision.message == "done"
