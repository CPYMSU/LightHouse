from __future__ import annotations

from io import StringIO

from rich.console import Console

from lighthouse.ui import SwissTerminal


def recorded_ui():
    stream = StringIO()
    console = Console(
        file=stream,
        width=112,
        color_system=None,
        force_terminal=False,
        highlight=False,
    )
    return SwissTerminal(console), stream


def test_swiss_masthead_and_capability_atlas():
    ui, stream = recorded_ui()
    ui.masthead(
        mode="system",
        workspace="workspace-12345678901234567890",
        project="/Users/adsin/LightHouse",
    )
    ui.capabilities(
        [
            {
                "command": "git diff",
                "tool_name": "system.git.diff.v1",
                "kernel": "system",
                "risk": "low",
                "writes": False,
            },
            {
                "command": "file patch",
                "tool_name": "system.file.patch.v1",
                "kernel": "system",
                "risk": "high",
                "writes": True,
            },
        ]
    )
    output = stream.getvalue()
    assert "LIGHTHOUSE OS" in output
    assert "AI OPERATING TERMINAL" in output
    assert "KERNEL" in output
    assert "CAPABILITY ATLAS" in output
    assert "system.file.patch.v1" in output


def test_run_timeline_renders_plan_execute_verify_and_dict_receipt():
    ui, stream = recorded_ui()
    snapshot = {
        "run": {
            "id": "run-123",
            "status": "succeeded",
            "final_message": "Tests pass and the diff is verified.",
        },
        "steps": [
            {"sequence": 1, "kind": "run_created", "payload": {"task": "Fix tests"}},
            {
                "sequence": 2,
                "kind": "decision",
                "payload": {
                    "kind": "tool",
                    "capability": "system.test.run.v1",
                    "reason": "verify",
                },
            },
            {
                "sequence": 3,
                "kind": "operation_dispatched",
                "payload": {
                    "capability": "system.test.run.v1",
                    "status": "succeeded",
                    "operation_id": "op-1",
                },
            },
            {
                "sequence": 4,
                "kind": "observation",
                "payload": {"receipt": {"ok": True, "result": {"exit_code": 0}}},
            },
            {
                "sequence": 5,
                "kind": "run_completed",
                "payload": {"message": "Verified"},
            },
        ],
    }
    seen = ui.render_run(snapshot)
    ui.final(snapshot)
    output = stream.getvalue()
    assert "PLAN" in output
    assert "THINK" in output
    assert "EXECUTE" in output
    assert "VERIFY" in output
    assert "RECEIPT OK" in output
    assert "SUCCEEDED / RECEIPT-BACKED" in output
    assert len(seen) == 5


def test_confirmation_card_exposes_frozen_target_and_arguments():
    ui, stream = recorded_ui()
    ui.confirmation(
        {
            "operation": {
                "id": "op-9",
                "capability": "system.file.patch.v1",
                "kernel": "system",
                "target_id": "target-1",
                "envelope": {"arguments": {"patch": "diff --git a/x b/x"}},
            }
        }
    )
    output = stream.getvalue()
    assert "CONFIRM / FROZEN OPERATION" in output
    assert "NO WRITE HAS OCCURRED" in output
    assert "system.file.patch.v1" in output
    assert "FROZEN ARGUMENTS" in output
