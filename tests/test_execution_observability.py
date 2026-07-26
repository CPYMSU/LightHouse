from __future__ import annotations

from io import StringIO

from rich.console import Console

from lighthouse.cognitive import build_cognitive_observer
from lighthouse.execution_observability import (
    ExecutionObservatoryTerminal,
    describe_tool_call,
)


def test_balanced_observe_shows_every_main_tool_start_and_result():
    steps = [
        {"sequence": 1, "kind": "run_created", "payload": {"task": "Inspect and fix code"}},
        {
            "sequence": 2,
            "kind": "decision",
            "payload": {
                "kind": "tool",
                "capability": "system.file.read.v1",
                "arguments": {"path": "src/app.py"},
            },
        },
        {
            "sequence": 3,
            "kind": "observation",
            "payload": {
                "capability": "system.file.read.v1",
                "receipt": {"ok": True, "result": {"path": "src/app.py"}},
            },
        },
        {
            "sequence": 4,
            "kind": "decision",
            "payload": {
                "kind": "tool",
                "capability": "system.test.run.v1",
                "arguments": {"command": "pytest -q"},
            },
        },
        {
            "sequence": 5,
            "kind": "observation",
            "payload": {
                "capability": "system.test.run.v1",
                "receipt": {"ok": False, "result": {"error": "one test failed"}},
            },
        },
    ]
    buffer = StringIO()
    ui = ExecutionObservatoryTerminal(
        console=Console(file=buffer, force_terminal=False, width=120),
        observe_mode="balanced",
    )
    snapshot = {
        "run": {"id": "run-1", "status": "running", "auto_confirm": True},
        "steps": steps,
        "cognitive_observer": build_cognitive_observer({"task": "Inspect and fix code"}, steps),
        "agent_observatory": {"active": 0, "total": 0, "items": []},
        "agent_execution_activity": [],
    }

    ui.render_run(snapshot)
    output = buffer.getvalue()

    assert "READ" in output
    assert "src/app.py" in output
    assert "STARTED" in output
    assert "SUCCEEDED" in output
    assert "TEST" in output
    assert "pytest -q" in output
    assert "FAILED" in output


def test_specialist_agent_tool_events_are_visible_and_deduplicated():
    buffer = StringIO()
    ui = ExecutionObservatoryTerminal(
        console=Console(file=buffer, force_terminal=False, width=140),
        observe_mode="balanced",
    )
    snapshot = {
        "run": {"id": "run-1", "status": "running", "auto_confirm": True},
        "steps": [],
        "cognitive_observer": {"state": {}, "timeline": [], "activity": []},
        "agent_observatory": {"active": 1, "total": 1, "items": []},
        "agent_execution_activity": [
            {
                "id": 101,
                "role": "backend",
                "payload": {
                    "label": "SEARCH",
                    "summary": "authorize_auto in src/lighthouse",
                    "status": "running",
                },
            },
            {
                "id": 102,
                "role": "backend",
                "payload": {
                    "label": "SEARCH",
                    "summary": "authorize_auto in src/lighthouse",
                    "status": "succeeded",
                },
            },
        ],
    }

    ui.render_run(snapshot)
    ui.render_run(snapshot)
    output = buffer.getvalue()

    assert output.count("A:BACKEND") == 2
    assert "SEARCH" in output
    assert "RUNNING" in output
    assert "SUCCEEDED" in output


def test_tool_descriptions_redact_credentials_before_durable_display():
    label, summary = describe_tool_call(
        "system.shell.exec.v1",
        {"command": "curl -H 'Authorization: Bearer top-secret-token' https://example.test"},
    )
    assert label == "EXEC"
    assert "top-secret-token" not in summary
    assert "[REDACTED]" in summary
