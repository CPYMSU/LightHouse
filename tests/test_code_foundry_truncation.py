from lighthouse.code_foundry import (
    CodeAction,
    CodeActionKind,
    CodeHistory,
    CodeObservation,
    formatted_truncate_text,
    truncate_middle_chars,
    truncate_middle_with_token_budget,
)


def test_middle_truncation_preserves_utf8_boundaries_and_both_ends():
    content = "😀😀😀😀😀😀😀😀😀😀\nsecond line with text\n"

    assert truncate_middle_chars(content, 20) == "😀😀…21 chars truncated…with text\n"
    assert truncate_middle_with_token_budget(content, 8) == (
        "😀😀😀😀…8 tokens truncated… line with text\n",
        16,
    )


def test_formatted_truncation_keeps_short_output_and_marks_long_output():
    assert formatted_truncate_text("short output", 100) == "short output"

    truncated = formatted_truncate_text("abcdef", 0)

    assert truncated.startswith("Warning: truncated output (original token count: 2)")
    assert truncated.endswith("…6 chars truncated…")


def test_history_bounds_only_model_facing_observation_payloads():
    history = CodeHistory()
    action = CodeAction(id="read", kind=CodeActionKind.READ)
    history.add_action(action)
    history.add_observation(
        CodeObservation(
            id="observation-read",
            action_id="read",
            kind=CodeActionKind.READ,
            ok=True,
            payload={"output": "a" * 1000},
        )
    )

    model_items = history.for_model(max_items=8, max_observation_bytes=32)
    model_output = model_items[-1].payload["payload"]["output"]

    assert model_output.startswith("Warning: truncated output")
    assert history.items()[-1].payload["payload"]["output"] == "a" * 1000
