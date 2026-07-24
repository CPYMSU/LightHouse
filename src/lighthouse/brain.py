from __future__ import annotations

from typing import Any

from .agent import AgentRuntime


class LightHouseBrain(AgentRuntime):
    """LightHouse's built-in reasoning and action loop.

    The brain is not an external agent product. It plans against the current
    LightHouse capability atlas, dispatches every action through OperationKernel,
    observes durable Receipts, verifies outcomes and resumes from PostgreSQL.
    """

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        run = self.repository.get_agent_run(run_id)
        workspace = self.repository.get_workspace(run.workspace_id)
        workspace_state = state.setdefault("workspace", {})
        workspace_state["desktop_target_id"] = workspace.desktop_target_id
        workspace_state["execution_surfaces"] = {
            "data": bool(workspace.data_target_id),
            "system": bool(workspace.system_target_id),
            "desktop": bool(workspace.desktop_target_id),
        }
        return state

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "You are the LightHouse AI operating-system brain. The governed worlds are "
            "Data (PostgreSQL), System (files/code/shell/servers), and Desktop "
            "(macOS applications, browsers, and confined files). A goal may require a "
            "sequence across multiple kernels when the run mode is auto. Prefer semantic "
            "Desktop capabilities over shell commands such as open, and never use pixel "
            "or coordinate guessing when an exact capability exists. "
            + base
        )


ReasoningLoop = LightHouseBrain
