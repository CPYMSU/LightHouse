from __future__ import annotations

import hmac
import json
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from .bootstrap import build_kernel, migration_sql
from .config import Settings
from .kernel import OperationKernel
from .models import KernelMode, OperationRequest, TargetKind
from .targets import validate_target_config


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    kind: TargetKind
    config: dict[str, Any]


class WorkspaceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128)
    data_target_id: str | None = None
    system_target_id: str | None = None


class OperationCreate(StrictModel):
    capability: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str
    actor: str = Field(min_length=1, max_length=128)
    mode: KernelMode = KernelMode.AUTO
    idempotency_key: str | None = Field(default=None, max_length=256)


class ConfirmRequest(StrictModel):
    actor: str = Field(min_length=1, max_length=128)


def _target_dict(target) -> dict[str, Any]:
    return {"id": target.id, "name": target.name, "kind": target.kind.value, "config": target.config, "active": target.active}


def _workspace_dict(workspace) -> dict[str, Any]:
    return {"id": workspace.id, "name": workspace.name, "data_target_id": workspace.data_target_id, "system_target_id": workspace.system_target_id}


def create_app(settings: Settings | None = None, kernel: OperationKernel | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    kernel = kernel or build_kernel(settings)
    repository = kernel.repository
    app = FastAPI(title="LightHouse OS", version="0.1.0")

    def require_operator(authorization: str | None = Header(default=None)) -> None:
        expected = "Bearer " + settings.api_key
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid operator credential")

    @app.exception_handler(KeyError)
    async def key_error(_request, exc: KeyError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    @app.exception_handler(PermissionError)
    async def permission_error(_request, exc: PermissionError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def value_error(_request, exc: ValueError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/admin/migrate", dependencies=[Depends(require_operator)])
    def migrate() -> dict[str, bool]:
        repository.migrate(migration_sql())
        return {"ok": True}

    @app.get("/v1/capabilities", dependencies=[Depends(require_operator)])
    def capabilities(q: str = "", kernel_mode: Literal["data", "system", "auto"] = Query(default="auto", alias="kernel"), limit: int = 50) -> dict[str, Any]:
        mode = KernelMode(kernel_mode)
        items = kernel.registry.search(q, kernel=mode, limit=limit) if q else kernel.registry.list(kernel=mode)
        return {"items": [item.public_dict() for item in items], "count": len(items)}

    @app.post("/v1/targets", dependencies=[Depends(require_operator)])
    def create_target(payload: TargetCreate) -> dict[str, Any]:
        config = validate_target_config(payload.kind, payload.config)
        return _target_dict(repository.create_target(name=payload.name, kind=payload.kind, config=config))

    @app.get("/v1/targets", dependencies=[Depends(require_operator)])
    def list_targets() -> dict[str, Any]:
        items = [_target_dict(item) for item in repository.list_targets()]
        return {"items": items, "count": len(items)}

    @app.post("/v1/workspaces", dependencies=[Depends(require_operator)])
    def create_workspace(payload: WorkspaceCreate) -> dict[str, Any]:
        if payload.data_target_id and repository.get_target(payload.data_target_id).kind != TargetKind.DATA:
            raise ValueError("data_target_id does not reference a data target")
        if payload.system_target_id and repository.get_target(payload.system_target_id).kind != TargetKind.SYSTEM:
            raise ValueError("system_target_id does not reference a system target")
        workspace = repository.create_workspace(name=payload.name, data_target_id=payload.data_target_id, system_target_id=payload.system_target_id)
        return _workspace_dict(workspace)

    @app.get("/v1/workspaces", dependencies=[Depends(require_operator)])
    def list_workspaces() -> dict[str, Any]:
        items = [_workspace_dict(item) for item in repository.list_workspaces()]
        return {"items": items, "count": len(items)}

    @app.post("/v1/operations", dependencies=[Depends(require_operator)])
    def create_operation(payload: OperationCreate) -> dict[str, Any]:
        request = OperationRequest(capability=payload.capability, arguments=payload.arguments, workspace_id=payload.workspace_id, actor=payload.actor, mode=payload.mode, idempotency_key=payload.idempotency_key)
        return kernel.submit(request)

    @app.get("/v1/operations/{operation_id}", dependencies=[Depends(require_operator)])
    def get_operation(operation_id: str) -> dict[str, Any]:
        return kernel.snapshot(operation_id)

    @app.post("/v1/operations/{operation_id}/confirm", dependencies=[Depends(require_operator)])
    def confirm_operation(operation_id: str, payload: ConfirmRequest) -> dict[str, Any]:
        return kernel.confirm(operation_id, actor=payload.actor)

    @app.get("/v1/operations/{operation_id}/events", dependencies=[Depends(require_operator)])
    def get_events(operation_id: str) -> dict[str, Any]:
        items = repository.list_events(operation_id)
        return {"items": items, "count": len(items)}

    @app.get("/v1/operations/{operation_id}/events.ndjson", dependencies=[Depends(require_operator)])
    def stream_events(operation_id: str):
        items = repository.list_events(operation_id)
        def generate():
            for item in items:
                yield json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"
        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.get("/v1/operations/{operation_id}/receipt", dependencies=[Depends(require_operator)])
    def get_receipt(operation_id: str) -> dict[str, Any]:
        receipt = repository.get_receipt(operation_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="receipt not available")
        return receipt

    return app
