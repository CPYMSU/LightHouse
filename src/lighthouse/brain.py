from __future__ import annotations

from typing import Any

from .agent import AgentRuntime


class LightHouseBrain(AgentRuntime):
    """LightHouse's built-in reasoning and action loop."""

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
        catalog = getattr(self.kernel, "data_catalog", None)
        if catalog is not None:
            try:
                data_worlds = catalog.context(workspace.id, resource_limit=80)
            except Exception as exc:
                data_worlds = {"available": False, "error": str(exc), "error_type": type(exc).__name__}
            state["data_worlds"] = data_worlds
            bindings = data_worlds.get("bindings") if isinstance(data_worlds, dict) else None
            workspace_state["execution_surfaces"]["data"] = bool(bindings) or bool(workspace.data_target_id)
        return state

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "You are the LightHouse AI operating-system brain. The governed worlds are "
            "Data (PostgreSQL), System (files/code/shell/servers), and Desktop "
            "(macOS applications, browsers, and confined files). A goal may require a "
            "sequence across multiple kernels when the run mode is auto. For Data work, "
            "prefer registered semantic commands first, then cataloged resource capabilities, "
            "and use raw SQL only when the typed surfaces cannot express the request. Before "
            "using a new data world, sync its catalog. Never invent resource names, columns, "
            "primary keys or semantic commands; use the data_worlds state and successful "
            "Receipts. Resource mutations are allowed only through explicitly write-enabled "
            "columns. Prefer semantic Desktop capabilities over shell commands such as open, "
            "and never use pixel or coordinate guessing when an exact capability exists. "
            + base
        )


ReasoningLoop = LightHouseBrain
