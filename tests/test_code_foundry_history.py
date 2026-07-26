from __future__ import annotations

from lighthouse.code_foundry import CodeAction, CodeActionKind, CodeHistory, CodeObservation


def test_patch_invalidates_unpinned_observations_for_changed_paths():
    history = CodeHistory()
    history.add_brief({"task": "Fix parser"})
    read_action = CodeAction(id="read-1", kind=CodeActionKind.READ, arguments={"path": "src/parser.py"})
    history.add_action(read_action)
    history.add_observation(
        CodeObservation(
            id="observation-read-1",
            action_id="read-1",
            kind=CodeActionKind.READ,
            ok=True,
            payload={"path": "src/parser.py", "content": "old source"},
        )
    )
    history.add_summary("Parser expects a string.")

    history.invalidate_paths(["src/parser.py"])

    active = history.items()
    all_items = history.items(include_stale=True)
    assert [item.kind.value for item in active] == ["brief", "action", "summary"]
    assert all_items[2].stale is True
    assert all_items[-1].pinned is True


def test_compaction_keeps_pinned_contract_and_recent_active_items():
    history = CodeHistory()
    history.add_brief({"task": "Fix parser"})
    for index in range(4):
        history.add_summary(f"Summary {index}")
    action = CodeAction(id="read", kind=CodeActionKind.READ, arguments={"path": "src/parser.py"})
    history.add_action(action)
    history.add_observation(
        CodeObservation(
            id="observation-read",
            action_id="read",
            kind=CodeActionKind.READ,
            ok=True,
            payload={"path": "src/parser.py"},
        )
    )

    compacted = history.compact(max_items=6)

    assert compacted[0].kind.value == "brief"
    assert [item.payload.get("content") for item in compacted if item.kind.value == "summary"] == [
        "Summary 0",
        "Summary 1",
        "Summary 2",
        "Summary 3",
    ]
    assert compacted[-1].kind.value == "observation"
