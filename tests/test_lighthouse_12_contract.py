from pathlib import Path


def test_terminal_offers_auto_only_at_governed_action_time():
    terminal_v4 = Path("src/lighthouse/terminal_v4.py").read_text(encoding="utf-8")
    terminal_v2 = Path("src/lighthouse/terminal_v2.py").read_text(encoding="utf-8")
    terminal_entry = Path("src/lighthouse/terminal_entry.py").read_text(encoding="utf-8")
    assert "_ask_auto_mode" not in terminal_v4
    assert '"auto_confirm": bool(auto_confirm)' in terminal_v4
    assert "/auto-authorize" in terminal_v2
    assert "permission_choice" in terminal_v2
    assert "ASK ON ACTION" in terminal_entry
    assert "terminal_v4" in terminal_entry


def test_terminal_exposes_agents_and_token_receipts():
    terminal = Path("src/lighthouse/terminal_v4.py").read_text(encoding="utf-8")
    ui = Path("src/lighthouse/ui_v12.py").read_text(encoding="utf-8")
    assert 'line == "/agents"' in terminal
    assert 'line == "/tokens"' in terminal
    assert "/v1/agent/runs/{run_id}/agents" in terminal
    assert "/v1/agent/runs/{run_id}/usage" in terminal
    assert "AGENT FIELD" in ui
    assert "TOKENS" in ui
    assert "COMPLETED_WITH_WARNING" in ui


def test_observatory_api_preserves_existing_api_and_adds_new_routes():
    api = Path("src/lighthouse/api_v12.py").read_text(encoding="utf-8")
    server = Path("src/lighthouse/server.py").read_text(encoding="utf-8")
    assert "create_base_app" in api
    assert "/v1/agent/runs/{run_id}/auto-authorize" in api
    assert "/v1/agent/runs/{run_id}/agents" in api
    assert "/v1/projects/{project_id}/massive-build" in api
    assert "from .api_v12 import create_app" in server


def test_massive_build_is_tools_not_a_fixed_workflow():
    capabilities = Path("src/lighthouse/massive_build_capabilities.py").read_text(encoding="utf-8")
    context = Path("src/lighthouse/mega_context.py").read_text(encoding="utf-8")
    prompt = Path("src/lighthouse/brain.py").read_text(encoding="utf-8")
    for tool in (
        "project.cell.create.v1",
        "project.contract.create.v1",
        "project.write_lease.acquire.v1",
        "project.worktree.create.v1",
        "project.batch.update.v1",
        "project.integration.update.v1",
        "project.wiring.verify.v1",
    ):
        assert tool in capabilities
    assert "fixed_workflow" in context
    assert "wait for all Agents" in prompt
    assert "work in parallel and review distilled results later" in prompt
