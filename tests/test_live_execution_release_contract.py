from pathlib import Path


def test_auto_authorization_continues_in_background_and_returns_to_polling():
    api = Path("src/lighthouse/api_v12.py").read_text(encoding="utf-8")
    terminal = Path("src/lighthouse/terminal_v2.py").read_text(encoding="utf-8")

    assert "class _AutoAuthorizeRequest" in api
    assert "background: bool = True" in api
    assert "Thread(" in api
    assert '"auto_authorization_background"' in api
    assert '{"actor": actor, "background": True}' in terminal
    assert "server-side Auto thread now continues the run" in terminal
    assert "every tool start, result and Receipt remains visible" in terminal
    assert "test_auto_authorization_returns_to_live_polling" in Path(
        "tests/test_auto_live_execution.py"
    ).read_text(encoding="utf-8")


def test_formal_terminal_streams_main_and_specialist_tool_receipts():
    terminal = Path("src/lighthouse/terminal_v4.py").read_text(encoding="utf-8")
    observability = Path("src/lighthouse/execution_observability.py").read_text(encoding="utf-8")
    brain = Path("src/lighthouse/mega_brain.py").read_text(encoding="utf-8")
    bootstrap = Path("src/lighthouse/bootstrap.py").read_text(encoding="utf-8")

    assert "ExecutionObservatoryTerminal as ObservatoryTerminal" in terminal
    assert "class ExecutionObservatoryTerminal" in observability
    assert "class ObservableBackgroundIntelligenceWorker" in observability
    assert '"agent_tool_started"' in observability
    assert '"agent_tool_completed"' in observability
    assert "AgentExecutionContextMixin" in brain
    assert "ObservableBackgroundIntelligenceWorker" in bootstrap


def test_balanced_activity_is_complete_but_secrets_are_redacted():
    observability = Path("src/lighthouse/execution_observability.py").read_text(encoding="utf-8")
    assert 'if self.observe_mode == "balanced":' in observability
    assert "return bool(label)" in observability
    assert "_SECRET_PATTERNS" in observability
    assert "[REDACTED]" in observability
    assert "Receipt-backed" in observability
