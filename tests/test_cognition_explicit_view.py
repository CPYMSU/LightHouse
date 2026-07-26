from io import StringIO

from rich.console import Console

from lighthouse.ui_v12 import ObservatoryTerminal


def test_explicit_cognition_view_renders_even_when_observation_is_off():
    buffer = StringIO()
    ui = ObservatoryTerminal(
        console=Console(file=buffer, force_terminal=False, width=120),
        observe_mode="off",
    )

    ui.cognition(
        {
            "observer": {
                "state": {
                    "active_work": {
                        "stage": "implementing",
                        "headline": "Integrate Cognitive Continuity into the active terminal",
                        "changed_files": ["src/lighthouse/ui_v12.py"],
                    },
                    "work_items": [
                        {
                            "id": "terminal",
                            "title": "Render durable cognitive state",
                            "status": "in_progress",
                        }
                    ],
                    "validation": {"passed": 1, "failed": 0, "running": 0},
                }
            }
        }
    )

    output = buffer.getvalue()
    assert "WORK STATE" in output
    assert "Integrate Cognitive Continuity" in output
    assert "Render durable cognitive state" in output
    assert ui.observe_mode == "off"
