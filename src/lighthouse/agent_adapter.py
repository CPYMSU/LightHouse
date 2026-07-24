from __future__ import annotations

from typing import Any

from .agent_store import AgentStore


class AgentRepositoryAdapter:
    """Presents durable agent state beside the existing operation repository.

    Keeping agent state in a narrow store lets the 0.1 Operation Repository stay
    stable while the coding runtime evolves independently.
    """

    def __init__(self, store: AgentStore, operation_repository: Any):
        self.store = store
        self.operation_repository = operation_repository

    def get_workspace(self, workspace_id: str):
        return self.operation_repository.get_workspace(workspace_id)

    def create_agent_run(self, **kwargs):
        return self.store.create_run(**kwargs)

    def get_agent_run(self, run_id: str):
        return self.store.get_run(run_id)

    def update_agent_run(self, run_id: str, **kwargs):
        return self.store.update_run(run_id, **kwargs)

    def append_agent_step(self, run_id: str, kind: str, payload: dict[str, Any]):
        return self.store.append_step(run_id, kind, payload)

    def list_agent_steps(self, run_id: str):
        return self.store.list_steps(run_id)
