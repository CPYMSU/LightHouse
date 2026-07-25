from __future__ import annotations

from dataclasses import replace
import json
from threading import RLock
from typing import Any, Protocol

from .models import AgentRunStatus, AgentRunView, KernelMode, utc_now


_UNSET = object()


class AgentStore(Protocol):
    def create_run(
        self,
        *,
        run_id: str,
        task: str,
        workspace_id: str,
        actor: str,
        mode: KernelMode,
        max_steps: int,
        auto_confirm: bool,
    ) -> AgentRunView: ...

    def get_run(self, run_id: str) -> AgentRunView: ...

    def update_run(
        self,
        run_id: str,
        *,
        status: AgentRunStatus | None = None,
        current_step: int | None = None,
        auto_confirm: bool | None = None,
        auto_scope: dict[str, Any] | None | object = _UNSET,
        pending_operation_id: str | None | object = _UNSET,
        final_message: str | None | object = _UNSET,
        execution_status: str | None = None,
        response_status: str | None = None,
        goal_status: str | None = None,
        warning: str | None | object = _UNSET,
    ) -> AgentRunView: ...

    def append_step(self, run_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def list_steps(self, run_id: str) -> list[dict[str, Any]]: ...


class InMemoryAgentStore:
    def __init__(self) -> None:
        self.runs: dict[str, AgentRunView] = {}
        self.steps: dict[str, list[dict[str, Any]]] = {}
        self._lock = RLock()

    def create_run(
        self,
        *,
        run_id: str,
        task: str,
        workspace_id: str,
        actor: str,
        mode: KernelMode,
        max_steps: int,
        auto_confirm: bool,
    ) -> AgentRunView:
        with self._lock:
            if run_id in self.runs:
                return self.runs[run_id]
            now = utc_now()
            run = AgentRunView(
                id=run_id,
                task=task,
                workspace_id=workspace_id,
                actor=actor,
                mode=mode,
                status=AgentRunStatus.CREATED,
                max_steps=max_steps,
                current_step=0,
                auto_confirm=auto_confirm,
                pending_operation_id=None,
                final_message=None,
                created_at=now,
                updated_at=now,
                execution_status="not_started",
                response_status="pending",
                goal_status="unknown",
                warning=None,
                auto_scope={},
            )
            self.runs[run_id] = run
            self.steps[run_id] = []
            return run

    def get_run(self, run_id: str) -> AgentRunView:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise KeyError("agent run not found") from exc

    def update_run(
        self,
        run_id: str,
        *,
        status: AgentRunStatus | None = None,
        current_step: int | None = None,
        auto_confirm: bool | None = None,
        auto_scope: dict[str, Any] | None | object = _UNSET,
        pending_operation_id: str | None | object = _UNSET,
        final_message: str | None | object = _UNSET,
        execution_status: str | None = None,
        response_status: str | None = None,
        goal_status: str | None = None,
        warning: str | None | object = _UNSET,
    ) -> AgentRunView:
        with self._lock:
            current = self.get_run(run_id)
            updated = replace(
                current,
                status=status or current.status,
                current_step=current.current_step if current_step is None else current_step,
                auto_confirm=current.auto_confirm if auto_confirm is None else bool(auto_confirm),
                auto_scope=current.auto_scope if auto_scope is _UNSET else dict(auto_scope or {}),
                pending_operation_id=(
                    current.pending_operation_id
                    if pending_operation_id is _UNSET
                    else pending_operation_id
                ),
                final_message=current.final_message if final_message is _UNSET else final_message,
                execution_status=execution_status or current.execution_status,
                response_status=response_status or current.response_status,
                goal_status=goal_status or current.goal_status,
                warning=current.warning if warning is _UNSET else warning,
                updated_at=utc_now(),
            )
            self.runs[run_id] = updated
            return updated

    def append_step(self, run_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.get_run(run_id)
            sequence = len(self.steps.setdefault(run_id, [])) + 1
            step = {
                "sequence": sequence,
                "kind": kind,
                "payload": payload,
                "created_at": utc_now().isoformat(),
            }
            self.steps[run_id].append(step)
            return step

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        return list(self.steps.get(run_id, []))


class PostgresAgentStore:
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("install lighthouse-os with PostgreSQL dependencies") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    @staticmethod
    def _run(row: dict[str, Any]) -> AgentRunView:
        return AgentRunView(
            id=str(row["id"]),
            task=row["task"],
            workspace_id=str(row["workspace_id"]),
            actor=row["actor"],
            mode=KernelMode(row["mode"]),
            status=AgentRunStatus(row["status"]),
            max_steps=row["max_steps"],
            current_step=row["current_step"],
            auto_confirm=row["auto_confirm"],
            pending_operation_id=(
                str(row["pending_operation_id"]) if row["pending_operation_id"] else None
            ),
            final_message=row["final_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            execution_status=str(row.get("execution_status") or "not_started"),
            response_status=str(row.get("response_status") or "pending"),
            goal_status=str(row.get("goal_status") or "unknown"),
            warning=row.get("warning"),
            auto_scope=dict(row.get("auto_scope") or {}),
        )

    def create_run(
        self,
        *,
        run_id: str,
        task: str,
        workspace_id: str,
        actor: str,
        mode: KernelMode,
        max_steps: int,
        auto_confirm: bool,
    ) -> AgentRunView:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_agent_runs(
                       id,task,workspace_id,actor,mode,status,max_steps,current_step,
                       auto_confirm,execution_status,response_status,goal_status,auto_scope
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,'not_started','pending','unknown','{}'::jsonb)
                   ON CONFLICT (id) DO NOTHING RETURNING *""",
                (
                    run_id,
                    task,
                    workspace_id,
                    actor,
                    mode.value,
                    AgentRunStatus.CREATED.value,
                    max_steps,
                    auto_confirm,
                ),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    "SELECT * FROM lh_agent_runs WHERE id=%s", (run_id,)
                ).fetchone()
        return self._run(row)

    def get_run(self, run_id: str) -> AgentRunView:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lh_agent_runs WHERE id=%s", (run_id,)
            ).fetchone()
        if not row:
            raise KeyError("agent run not found")
        return self._run(row)

    def update_run(
        self,
        run_id: str,
        *,
        status: AgentRunStatus | None = None,
        current_step: int | None = None,
        auto_confirm: bool | None = None,
        auto_scope: dict[str, Any] | None | object = _UNSET,
        pending_operation_id: str | None | object = _UNSET,
        final_message: str | None | object = _UNSET,
        execution_status: str | None = None,
        response_status: str | None = None,
        goal_status: str | None = None,
        warning: str | None | object = _UNSET,
    ) -> AgentRunView:
        current = self.get_run(run_id)
        next_pending = current.pending_operation_id if pending_operation_id is _UNSET else pending_operation_id
        next_message = current.final_message if final_message is _UNSET else final_message
        next_scope = current.auto_scope if auto_scope is _UNSET else dict(auto_scope or {})
        next_warning = current.warning if warning is _UNSET else warning
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_agent_runs SET
                     status=%s,current_step=%s,auto_confirm=%s,auto_scope=%s::jsonb,
                     pending_operation_id=%s,final_message=%s,execution_status=%s,
                     response_status=%s,goal_status=%s,warning=%s,updated_at=now()
                   WHERE id=%s RETURNING *""",
                (
                    (status or current.status).value,
                    current.current_step if current_step is None else current_step,
                    current.auto_confirm if auto_confirm is None else bool(auto_confirm),
                    json.dumps(next_scope, ensure_ascii=False, default=str),
                    next_pending,
                    next_message,
                    execution_status or current.execution_status,
                    response_status or current.response_status,
                    goal_status or current.goal_status,
                    next_warning,
                    run_id,
                ),
            ).fetchone()
        if not row:
            raise KeyError("agent run not found")
        return self._run(row)

    def append_step(self, run_id: str, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            locked = connection.execute(
                "SELECT id FROM lh_agent_runs WHERE id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if not locked:
                raise KeyError("agent run not found")
            row = connection.execute(
                """INSERT INTO lh_agent_steps(run_id,sequence,kind,payload)
                   SELECT %s,COALESCE(MAX(sequence),0)+1,%s,%s::jsonb
                   FROM lh_agent_steps WHERE run_id=%s
                   RETURNING sequence,kind,payload,created_at""",
                (run_id, kind, json.dumps(payload, ensure_ascii=False, default=str), run_id),
            ).fetchone()
        return {
            "sequence": row["sequence"],
            "kind": row["kind"],
            "payload": row["payload"],
            "created_at": row["created_at"].isoformat(),
        }

    def list_steps(self, run_id: str) -> list[dict[str, Any]]:
        self.get_run(run_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT sequence,kind,payload,created_at FROM lh_agent_steps "
                "WHERE run_id=%s ORDER BY sequence",
                (run_id,),
            ).fetchall()
        return [
            {
                "sequence": row["sequence"],
                "kind": row["kind"],
                "payload": row["payload"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]
