from __future__ import annotations

import hmac
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .api import create_app as create_base_app
from .bootstrap import build_agent_runtime, build_kernel
from .config import Settings
from .kernel import OperationKernel


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ActorRequest(_StrictModel):
    actor: str = Field(min_length=1, max_length=128)


def create_app(
    settings: Settings | None = None,
    kernel: OperationKernel | None = None,
    agent_runtime=None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    kernel = kernel or build_kernel(settings)
    agent_runtime = agent_runtime or build_agent_runtime(settings, kernel)
    app = create_base_app(settings, kernel, agent_runtime)
    agent_bus = getattr(agent_runtime, "agent_bus", None)
    usage_store = getattr(agent_runtime, "usage_store", None)
    massive_build = getattr(agent_runtime, "massive_build", None)

    def require_operator(authorization: str | None = Header(default=None)) -> None:
        expected = "Bearer " + settings.api_key
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid operator credential")

    def require_agent_bus():
        if agent_bus is None:
            raise HTTPException(status_code=409, detail="Agent Bus is not configured")
        return agent_bus

    def require_usage():
        if usage_store is None:
            raise HTTPException(status_code=409, detail="Model usage tracking is not configured")
        return usage_store

    def require_massive_build():
        if massive_build is None:
            raise HTTPException(status_code=409, detail="Massive Build is not configured")
        return massive_build

    @app.post(
        "/v1/agent/runs/{run_id}/auto-authorize",
        dependencies=[Depends(require_operator)],
    )
    def auto_authorize_run(run_id: str, payload: _ActorRequest) -> dict[str, Any]:
        return agent_runtime.authorize_auto(run_id, actor=payload.actor)

    @app.get(
        "/v1/agent/runs/{run_id}/agents",
        dependencies=[Depends(require_operator)],
    )
    def run_agents(run_id: str) -> dict[str, Any]:
        snapshot = agent_runtime.snapshot(run_id)
        return {
            "run_id": run_id,
            "observatory": snapshot.get("agent_observatory") or {},
            "coordination_advice": snapshot.get("coordination_advice") or {},
        }

    @app.get(
        "/v1/agent/runs/{run_id}/usage",
        dependencies=[Depends(require_operator)],
    )
    def run_usage(run_id: str) -> dict[str, Any]:
        snapshot = agent_runtime.snapshot(run_id)
        return snapshot.get("token_usage") or {
            "turn": require_usage().summary(run_id=run_id),
            "conversation": require_usage().summary(run_id=run_id),
        }

    @app.get(
        "/v1/conversations/{conversation_id}/usage",
        dependencies=[Depends(require_operator)],
    )
    def conversation_usage(conversation_id: str) -> dict[str, Any]:
        return require_usage().summary(conversation_id=conversation_id)

    @app.get(
        "/v1/projects/{project_id}/usage",
        dependencies=[Depends(require_operator)],
    )
    def project_usage(project_id: str) -> dict[str, Any]:
        return require_usage().summary(project_id=project_id)

    @app.get(
        "/v1/projects/{project_id}/massive-build",
        dependencies=[Depends(require_operator)],
    )
    def project_massive_build(project_id: str) -> dict[str, Any]:
        return require_massive_build().project_brief(project_id)

    @app.get(
        "/v1/agent-bus/work-orders/{work_order_id}/events",
        dependencies=[Depends(require_operator)],
    )
    def work_order_events(work_order_id: str, after_id: int = 0) -> dict[str, Any]:
        items = require_agent_bus().work_events(work_order_id, after_id=after_id)
        return {"items": items, "count": len(items)}

    @app.get(
        "/v1/agent-bus/coordination",
        dependencies=[Depends(require_operator)],
    )
    def coordination(
        workspace_id: str,
        run_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        return require_agent_bus().coordination_advice(
            workspace_id=workspace_id,
            parent_run_id=run_id,
            project_id=project_id,
        )

    return app
