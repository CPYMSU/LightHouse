from __future__ import annotations

import hmac
import json
from typing import Any, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .agent import AgentRuntime
from .bootstrap import build_agent_runtime, build_kernel, migration_sql
from .config import Settings
from .deferred_runs import DeferredRunScheduler
from .kernel import OperationKernel
from .models import KernelMode, OperationRequest, TargetKind
from .provider import AgentProtocolError, ModelNotConfiguredError
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
    desktop_target_id: str | None = None


class OperationCreate(StrictModel):
    capability: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    workspace_id: str
    actor: str = Field(min_length=1, max_length=128)
    mode: KernelMode = KernelMode.AUTO
    idempotency_key: str | None = Field(default=None, max_length=256)


class ConfirmRequest(StrictModel):
    actor: str = Field(min_length=1, max_length=128)


class AgentRunCreate(StrictModel):
    task: str = Field(min_length=1, max_length=20000)
    workspace_id: str
    actor: str = Field(min_length=1, max_length=128)
    mode: KernelMode = KernelMode.AUTO
    max_steps: int | None = Field(default=None, ge=1, le=64)
    auto_confirm: bool = False
    conversation_id: str | None = None
    new_conversation: bool = False
    work_intensity: Literal["quick", "balanced", "advanced", "extreme"] = "balanced"


class AgentInput(StrictModel):
    actor: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=20000)


class MemoryScanRequest(StrictModel):
    workspace_id: str
    max_files: int = Field(default=5000, ge=1, le=20000)


def _target_dict(target) -> dict[str, Any]:
    return {
        "id": target.id,
        "name": target.name,
        "kind": target.kind.value,
        "config": target.config,
        "active": target.active,
    }


def _workspace_dict(workspace) -> dict[str, Any]:
    return {
        "id": workspace.id,
        "name": workspace.name,
        "data_target_id": workspace.data_target_id,
        "system_target_id": workspace.system_target_id,
        "desktop_target_id": workspace.desktop_target_id,
        "config": getattr(workspace, "config", {}),
    }


def create_app(
    settings: Settings | None = None,
    kernel: OperationKernel | None = None,
    agent_runtime: AgentRuntime | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    kernel = kernel or build_kernel(settings)
    agent_runtime = agent_runtime or build_agent_runtime(settings, kernel)
    repository = kernel.repository
    memory = getattr(agent_runtime, "memory", None)
    agent_bus = getattr(agent_runtime, "agent_bus", None)
    context_compiler = getattr(agent_runtime, "context_compiler", None)
    app = FastAPI(title="LightHouse OS", version=__version__)
    run_scheduler = DeferredRunScheduler(agent_runtime) if memory is not None else None

    def require_operator(authorization: str | None = Header(default=None)) -> None:
        expected = "Bearer " + settings.api_key
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="invalid operator credential")

    def require_memory():
        if memory is None:
            raise HTTPException(status_code=409, detail="Memory Fabric is not configured")
        return memory

    def require_agent_bus():
        if agent_bus is None:
            raise HTTPException(status_code=409, detail="Agent Bus is not configured")
        return agent_bus

    @app.on_event("shutdown")
    def stop_background_worker() -> None:
        worker = getattr(agent_runtime, "background_worker", None)
        if worker is not None:
            worker.stop()

    @app.exception_handler(KeyError)
    async def key_error(_request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc).strip("'")})

    @app.exception_handler(PermissionError)
    async def permission_error(_request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def value_error(_request, exc: ValueError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ModelNotConfiguredError)
    async def model_not_configured(_request, exc: ModelNotConfiguredError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(AgentProtocolError)
    async def agent_protocol_error(_request, exc: AgentProtocolError):
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/v1/admin/migrate", dependencies=[Depends(require_operator)])
    def migrate() -> dict[str, bool]:
        migrate_fn = getattr(repository, "migrate", None)
        if not callable(migrate_fn):
            raise HTTPException(status_code=409, detail="repository has no migration interface")
        migrate_fn(migration_sql())
        return {"ok": True}

    @app.get("/v1/capabilities", dependencies=[Depends(require_operator)])
    def capabilities(
        q: str = "",
        kernel_mode: Literal["data", "system", "desktop", "auto"] = Query(
            default="auto",
            alias="kernel",
        ),
        limit: int = 50,
    ) -> dict[str, Any]:
        mode = KernelMode(kernel_mode)
        items = (
            kernel.registry.search(q, kernel=mode, limit=limit)
            if q
            else kernel.registry.list(kernel=mode)
        )
        return {"items": [item.public_dict() for item in items], "count": len(items)}

    @app.post("/v1/targets", dependencies=[Depends(require_operator)])
    def create_target(payload: TargetCreate) -> dict[str, Any]:
        config = validate_target_config(payload.kind, payload.config)
        return _target_dict(
            repository.create_target(
                name=payload.name,
                kind=payload.kind,
                config=config,
            )
        )

    @app.get("/v1/targets", dependencies=[Depends(require_operator)])
    def list_targets() -> dict[str, Any]:
        items = [_target_dict(item) for item in repository.list_targets()]
        return {"items": items, "count": len(items)}

    @app.post("/v1/workspaces", dependencies=[Depends(require_operator)])
    def create_workspace(payload: WorkspaceCreate) -> dict[str, Any]:
        for target_id, expected, field in (
            (payload.data_target_id, TargetKind.DATA, "data_target_id"),
            (payload.system_target_id, TargetKind.SYSTEM, "system_target_id"),
            (payload.desktop_target_id, TargetKind.DESKTOP, "desktop_target_id"),
        ):
            if target_id and repository.get_target(target_id).kind != expected:
                raise ValueError(f"{field} does not reference a {expected.value} target")
        workspace = repository.create_workspace(
            name=payload.name,
            data_target_id=payload.data_target_id,
            system_target_id=payload.system_target_id,
            desktop_target_id=payload.desktop_target_id,
        )
        return _workspace_dict(workspace)

    @app.get("/v1/workspaces", dependencies=[Depends(require_operator)])
    def list_workspaces() -> dict[str, Any]:
        items = [_workspace_dict(item) for item in repository.list_workspaces()]
        return {"items": items, "count": len(items)}

    @app.post("/v1/operations", dependencies=[Depends(require_operator)])
    def create_operation(payload: OperationCreate) -> dict[str, Any]:
        return kernel.submit(
            OperationRequest(
                capability=payload.capability,
                arguments=payload.arguments,
                workspace_id=payload.workspace_id,
                actor=payload.actor,
                mode=payload.mode,
                idempotency_key=payload.idempotency_key,
            )
        )

    @app.get("/v1/operations/{operation_id}", dependencies=[Depends(require_operator)])
    def get_operation(operation_id: str) -> dict[str, Any]:
        return kernel.snapshot(operation_id)

    @app.post(
        "/v1/operations/{operation_id}/confirm",
        dependencies=[Depends(require_operator)],
    )
    def confirm_operation(operation_id: str, payload: ConfirmRequest) -> dict[str, Any]:
        return kernel.confirm(operation_id, actor=payload.actor)

    @app.post(
        "/v1/operations/{operation_id}/confirm-deferred",
        dependencies=[Depends(require_operator)],
    )
    def confirm_operation_deferred(
        operation_id: str,
        payload: ConfirmRequest,
    ) -> dict[str, Any]:
        return kernel.confirm_deferred(operation_id, actor=payload.actor)

    @app.get(
        "/v1/operations/{operation_id}/events",
        dependencies=[Depends(require_operator)],
    )
    def get_events(operation_id: str) -> dict[str, Any]:
        items = repository.list_events(operation_id)
        return {"items": items, "count": len(items)}

    @app.get(
        "/v1/operations/{operation_id}/events.ndjson",
        dependencies=[Depends(require_operator)],
    )
    def stream_events(operation_id: str):
        items = repository.list_events(operation_id)

        def generate():
            for item in items:
                yield json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.get(
        "/v1/operations/{operation_id}/receipt",
        dependencies=[Depends(require_operator)],
    )
    def get_receipt(operation_id: str) -> dict[str, Any]:
        receipt = repository.get_receipt(operation_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="receipt not available")
        return receipt

    @app.post("/v1/agent/runs", dependencies=[Depends(require_operator)])
    def start_agent(payload: AgentRunCreate) -> dict[str, Any]:
        if run_scheduler is None:
            return agent_runtime.start(
                task=payload.task,
                workspace_id=payload.workspace_id,
                actor=payload.actor,
                mode=payload.mode,
                max_steps=payload.max_steps,
                auto_confirm=payload.auto_confirm,
                work_intensity=payload.work_intensity,
            )
        return run_scheduler.start(
            task=payload.task,
            workspace_id=payload.workspace_id,
            actor=payload.actor,
            mode=payload.mode,
            max_steps=payload.max_steps,
            auto_confirm=payload.auto_confirm,
            conversation_id=payload.conversation_id,
            new_conversation=payload.new_conversation,
            work_intensity=payload.work_intensity,
        )

    @app.get("/v1/agent/runs/{run_id}", dependencies=[Depends(require_operator)])
    def get_agent_run(run_id: str) -> dict[str, Any]:
        value = agent_runtime.snapshot(run_id)
        if run_scheduler is not None:
            value["brain_active"] = run_scheduler.is_active(run_id)
        return value

    @app.post(
        "/v1/agent/runs/{run_id}/advance",
        dependencies=[Depends(require_operator)],
    )
    def advance_agent(run_id: str) -> dict[str, Any]:
        if run_scheduler is None:
            return agent_runtime.advance(run_id)
        return run_scheduler.advance(run_id)

    @app.post(
        "/v1/agent/runs/{run_id}/input",
        dependencies=[Depends(require_operator)],
    )
    def agent_input(run_id: str, payload: AgentInput) -> dict[str, Any]:
        if run_scheduler is None:
            return agent_runtime.provide_input(
                run_id,
                actor=payload.actor,
                message=payload.message,
            )
        return run_scheduler.provide_input(
            run_id,
            actor=payload.actor,
            message=payload.message,
        )

    @app.get(
        "/v1/agent/runs/{run_id}/events.ndjson",
        dependencies=[Depends(require_operator)],
    )
    def stream_agent_events(run_id: str):
        items = agent_runtime.snapshot(run_id)["steps"]

        def generate():
            for item in items:
                yield json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n"

        return StreamingResponse(generate(), media_type="application/x-ndjson")

    @app.post("/v1/memory/scan", dependencies=[Depends(require_operator)])
    def scan_memory(payload: MemoryScanRequest) -> dict[str, Any]:
        fabric = require_memory()
        workspace = repository.get_workspace(payload.workspace_id)
        if not workspace.system_target_id:
            raise ValueError("workspace has no system target to index")
        target = repository.get_target(workspace.system_target_id)
        if target.kind != TargetKind.SYSTEM:
            raise ValueError("workspace system target is invalid")
        roots = target.config.get("allowed_roots") or [
            target.config.get("default_cwd") or "/"
        ]
        if agent_bus is None:
            return fabric.scan_workspace(
                workspace_id=workspace.id,
                roots=roots,
                max_files=payload.max_files,
            )
        job = agent_bus.enqueue_background_job(
            workspace_id=workspace.id,
            job_type="memory.workspace.scan",
            payload={"roots": roots, "max_files": payload.max_files},
            coalesce_key=f"workspace-scan:{workspace.id}",
            priority=10,
        )
        return {
            "queued": True,
            "job": job,
            "workspace_id": workspace.id,
            "indexed": 0,
            "directories_indexed": 0,
            "skipped": 0,
        }

    @app.get("/v1/memory/files", dependencies=[Depends(require_operator)])
    def memory_files(
        workspace_id: str,
        q: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        fabric = require_memory()
        items = fabric.search_files(
            workspace_id=workspace_id,
            query=q,
            limit=limit,
        )
        return {"items": items, "count": len(items)}

    @app.get("/v1/memory/context", dependencies=[Depends(require_operator)])
    def memory_context(
        workspace_id: str,
        actor: str,
        q: str = "",
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        fabric = require_memory()
        if context_compiler is not None:
            return context_compiler.compile(
                workspace_id=workspace_id,
                actor=actor,
                conversation_id=conversation_id,
                run_id=run_id,
                query=q,
            )
        return fabric.context(
            workspace_id=workspace_id,
            actor=actor,
            conversation_id=conversation_id,
            query=q,
            message_limit=24,
            file_limit=24,
        )

    @app.get("/v1/agent-bus/status", dependencies=[Depends(require_operator)])
    def agent_bus_status(workspace_id: str | None = None) -> dict[str, Any]:
        return require_agent_bus().status(workspace_id=workspace_id)

    @app.get("/v1/agent-bus/work-orders", dependencies=[Depends(require_operator)])
    def agent_bus_work_orders(
        workspace_id: str,
        run_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        items = require_agent_bus().list_work_orders(
            workspace_id=workspace_id,
            parent_run_id=run_id,
            limit=limit,
        )
        return {"items": items, "count": len(items)}

    return app
