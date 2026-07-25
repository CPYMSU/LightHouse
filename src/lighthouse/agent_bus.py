from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import time
from typing import Any
from uuid import uuid4


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PostgresAgentBus:
    """Durable registry, work-order bus and background queue for LightHouse agents."""

    BUILTIN_AGENTS: tuple[dict[str, Any], ...] = (
        {
            "name": "memory-steward",
            "role": "memory-steward",
            "execution_mode": "background",
            "visibility": "hidden",
            "capabilities": [
                "memory.conversation.distill",
                "memory.context.refresh",
                "memory.file.index",
                "memory.workspace.scan",
            ],
            "metadata": {"purpose": "Index, compact and distill durable context without blocking the foreground."},
            "max_concurrency": 1,
        },
        {
            "name": "context-investigator",
            "role": "context-investigator",
            "execution_mode": "deterministic",
            "visibility": "hidden",
            "capabilities": ["context.compile", "context.refresh"],
            "metadata": {"purpose": "Assemble verified facts, candidates and uncertainties for the main AI."},
            "max_concurrency": 4,
        },
        {
            "name": "file-reality-agent",
            "role": "file-reality",
            "execution_mode": "deterministic",
            "visibility": "hidden",
            "capabilities": ["system.path.inspect"],
            "metadata": {"purpose": "Inspect real filesystem state without modifying user files."},
            "max_concurrency": 4,
        },
        {
            "name": "authorization-agent",
            "role": "authorization",
            "execution_mode": "deterministic",
            "visibility": "hidden",
            "capabilities": ["policy.inspect"],
            "metadata": {"purpose": "Explain current workspace, target and capability constraints."},
            "max_concurrency": 4,
        },
        {
            "name": "receipt-verification-agent",
            "role": "receipt-verification",
            "execution_mode": "deterministic",
            "visibility": "hidden",
            "capabilities": ["receipt.inspect"],
            "metadata": {"purpose": "Separate execution evidence from goal-completion evidence."},
            "max_concurrency": 4,
        },
        {
            "name": "design-agent",
            "role": "design",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [],
            "metadata": {"purpose": "Produce focused design analysis and recommendations for the main AI."},
            "max_concurrency": 1,
        },
        {
            "name": "coding-agent",
            "role": "coding",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [],
            "metadata": {"purpose": "Produce implementation analysis and proposed changes for the main AI to apply."},
            "max_concurrency": 1,
        },
        {
            "name": "verification-agent",
            "role": "verification",
            "execution_mode": "model",
            "visibility": "foreground",
            "capabilities": [],
            "metadata": {"purpose": "Review evidence, gaps and validation strategy before the main AI declares completion."},
            "max_concurrency": 1,
        },
    )

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Agent Bus requires psycopg") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def register_builtin_agents(self) -> None:
        for item in self.BUILTIN_AGENTS:
            self.register_agent(**item)

    def register_agent(
        self,
        *,
        name: str,
        role: str,
        execution_mode: str,
        visibility: str = "foreground",
        capabilities: list[str] | tuple[str, ...] = (),
        metadata: dict[str, Any] | None = None,
        max_concurrency: int = 1,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_agents(
                       id,name,role,execution_mode,visibility,capabilities,metadata,
                       active,health,max_concurrency
                   ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,TRUE,'ready',%s)
                   ON CONFLICT (name) DO UPDATE SET
                     role=EXCLUDED.role,
                     execution_mode=EXCLUDED.execution_mode,
                     visibility=EXCLUDED.visibility,
                     capabilities=EXCLUDED.capabilities,
                     metadata=lh_agents.metadata || EXCLUDED.metadata,
                     active=TRUE,
                     max_concurrency=EXCLUDED.max_concurrency,
                     updated_at=now()
                   RETURNING *""",
                (
                    str(uuid4()),
                    str(name),
                    str(role),
                    str(execution_mode),
                    str(visibility),
                    _json(list(capabilities)),
                    _json(metadata or {}),
                    max(1, min(int(max_concurrency), 64)),
                ),
            ).fetchone()
        return self._agent_dict(row)

    def list_agents(self, *, include_hidden: bool = True) -> list[dict[str, Any]]:
        clauses = ["active=TRUE"]
        if not include_hidden:
            clauses.append("visibility='foreground'")
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM lh_agents WHERE {' AND '.join(clauses)}
                    ORDER BY role,name"""
            ).fetchall()
        return [self._agent_dict(row) for row in rows]

    def dispatch(
        self,
        *,
        workspace_id: str,
        requested_by: str,
        role: str,
        goal: str,
        parent_run_id: str | None = None,
        payload: dict[str, Any] | None = None,
        priority: int = 50,
        visibility: str = "foreground",
    ) -> dict[str, Any]:
        role = str(role or "").strip()
        goal = str(goal or "").strip()
        if not role:
            raise ValueError("agent role is required")
        if not goal:
            raise ValueError("work-order goal is required")
        with self._connect() as connection:
            agent = connection.execute(
                """SELECT * FROM lh_agents
                   WHERE role=%s AND active=TRUE AND health <> 'offline'
                   ORDER BY (health='ready') DESC,updated_at DESC LIMIT 1""",
                (role,),
            ).fetchone()
            if not agent:
                raise ValueError(f"no active agent is registered for role: {role}")
            row = connection.execute(
                """INSERT INTO lh_work_orders(
                       id,workspace_id,parent_run_id,requested_by,role,agent_id,goal,
                       payload,priority,visibility,status
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,'queued')
                   RETURNING *""",
                (
                    str(uuid4()),
                    workspace_id,
                    parent_run_id,
                    str(requested_by or "main-ai"),
                    role,
                    agent["id"],
                    goal,
                    _json(payload or {}),
                    max(0, min(int(priority), 100)),
                    "hidden" if visibility == "hidden" else "foreground",
                ),
            ).fetchone()
            connection.execute(
                """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                   VALUES (%s,'work_queued',%s::jsonb)""",
                (row["id"], _json({"role": role, "requested_by": requested_by})),
            )
        return self._work_dict(row)

    def list_work_orders(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        clauses = ["workspace_id=%s"]
        params: list[Any] = [workspace_id]
        if parent_run_id:
            clauses.append("parent_run_id=%s")
            params.append(parent_run_id)
        params.append(max(1, min(int(limit), 100)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM lh_work_orders
                    WHERE {' AND '.join(clauses)}
                    ORDER BY updated_at DESC LIMIT %s""",
                params,
            ).fetchall()
        return [self._work_dict(row) for row in rows]

    def get_work_order(self, work_order_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lh_work_orders WHERE id=%s",
                (work_order_id,),
            ).fetchone()
        if not row:
            raise KeyError("work order not found")
        return self._work_dict(row)

    def wait_for_work_order(
        self,
        work_order_id: str,
        *,
        timeout: float = 0,
        poll_interval: float = 0.25,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.0, min(float(timeout), 30.0))
        while True:
            value = self.get_work_order(work_order_id)
            if value["status"] in {"succeeded", "failed", "cancelled", "superseded"}:
                return value
            if time.monotonic() >= deadline:
                return value
            time.sleep(max(0.05, min(float(poll_interval), 2.0)))

    def cancel(self, work_order_id: str, *, requested_by: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_work_orders SET status='cancelled',error=%s,
                   completed_at=now(),updated_at=now()
                   WHERE id=%s AND status IN ('queued','leased','running','waiting_dependency')
                   RETURNING *""",
                (f"cancelled by {requested_by}", work_order_id),
            ).fetchone()
            if row:
                connection.execute(
                    """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                       VALUES (%s,'work_cancelled',%s::jsonb)""",
                    (work_order_id, _json({"requested_by": requested_by})),
                )
        if not row:
            return self.get_work_order(work_order_id)
        return self._work_dict(row)

    def claim_work_order(
        self,
        *,
        worker_id: str,
        execution_modes: tuple[str, ...] = ("model",),
        lease_seconds: int = 120,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """WITH candidate AS (
                     SELECT wo.id FROM lh_work_orders wo
                     JOIN lh_agents a ON a.id=wo.agent_id
                     WHERE wo.status='queued'
                       AND a.execution_mode = ANY(%s)
                       AND NOT EXISTS (
                         SELECT 1 FROM lh_work_dependencies d
                         JOIN lh_work_orders dep ON dep.id=d.depends_on_id
                         WHERE d.work_order_id=wo.id AND dep.status <> 'succeeded'
                       )
                     ORDER BY wo.priority DESC,wo.created_at
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE lh_work_orders wo SET
                     status='leased',lease_owner=%s,leased_at=now(),
                     lease_expires_at=now()+(%s || ' seconds')::interval,
                     heartbeat_at=now(),updated_at=now()
                   FROM candidate WHERE wo.id=candidate.id
                   RETURNING wo.*""",
                (list(execution_modes), worker_id, max(15, min(int(lease_seconds), 3600))),
            ).fetchone()
            if row:
                connection.execute(
                    """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                       VALUES (%s,'work_leased',%s::jsonb)""",
                    (row["id"], _json({"worker_id": worker_id})),
                )
        return self._work_dict(row) if row else None

    def mark_running(self, work_order_id: str, *, worker_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_work_orders SET status='running',heartbeat_at=now(),updated_at=now()
                   WHERE id=%s AND lease_owner=%s AND status IN ('leased','running')
                   RETURNING *""",
                (work_order_id, worker_id),
            ).fetchone()
        if not row:
            raise ValueError("work order lease is not owned by this worker")
        return self._work_dict(row)

    def complete(self, work_order_id: str, *, result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_work_orders SET status='succeeded',result=%s::jsonb,error=NULL,
                   completed_at=now(),updated_at=now(),heartbeat_at=now()
                   WHERE id=%s RETURNING *""",
                (_json(result), work_order_id),
            ).fetchone()
            if not row:
                raise KeyError("work order not found")
            connection.execute(
                """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                   VALUES (%s,'work_succeeded',%s::jsonb)""",
                (work_order_id, _json({"result": result})),
            )
        return self._work_dict(row)

    def fail(self, work_order_id: str, *, error: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_work_orders SET status='failed',error=%s,
                   completed_at=now(),updated_at=now(),heartbeat_at=now()
                   WHERE id=%s RETURNING *""",
                (str(error), work_order_id),
            ).fetchone()
            if not row:
                raise KeyError("work order not found")
            connection.execute(
                """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                   VALUES (%s,'work_failed',%s::jsonb)""",
                (work_order_id, _json({"error": str(error)})),
            )
        return self._work_dict(row)

    def enqueue_background_job(
        self,
        *,
        workspace_id: str,
        job_type: str,
        payload: dict[str, Any] | None = None,
        conversation_id: str | None = None,
        run_id: str | None = None,
        work_order_id: str | None = None,
        coalesce_key: str | None = None,
        priority: int = 20,
        delay_seconds: float = 0,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        run_after = _utc_now() + timedelta(seconds=max(0.0, float(delay_seconds)))
        with self._connect() as connection:
            if coalesce_key:
                row = connection.execute(
                    """SELECT * FROM lh_background_jobs
                       WHERE workspace_id=%s AND coalesce_key=%s
                         AND status IN ('pending','running')
                       ORDER BY created_at DESC LIMIT 1""",
                    (workspace_id, coalesce_key),
                ).fetchone()
                if row and row["status"] == "pending":
                    row = connection.execute(
                        """UPDATE lh_background_jobs SET payload=%s::jsonb,priority=GREATEST(priority,%s),
                           run_after=LEAST(run_after,%s),updated_at=now(),
                           conversation_id=COALESCE(%s,conversation_id),
                           run_id=COALESCE(%s,run_id),
                           work_order_id=COALESCE(%s,work_order_id)
                           WHERE id=%s RETURNING *""",
                        (
                            _json(payload or {}),
                            max(0, min(int(priority), 100)),
                            run_after,
                            conversation_id,
                            run_id,
                            work_order_id,
                            row["id"],
                        ),
                    ).fetchone()
                    return self._job_dict(row)
                if row:
                    return self._job_dict(row)
            row = connection.execute(
                """INSERT INTO lh_background_jobs(
                       id,workspace_id,conversation_id,run_id,work_order_id,job_type,
                       coalesce_key,payload,priority,status,run_after
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'pending',%s)
                   RETURNING *""",
                (
                    job_id,
                    workspace_id,
                    conversation_id,
                    run_id,
                    work_order_id,
                    str(job_type),
                    coalesce_key,
                    _json(payload or {}),
                    max(0, min(int(priority), 100)),
                    run_after,
                ),
            ).fetchone()
        return self._job_dict(row)

    def claim_background_job(
        self,
        *,
        worker_id: str,
        lease_seconds: int = 120,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """WITH candidate AS (
                     SELECT id FROM lh_background_jobs
                     WHERE status='pending' AND run_after <= now()
                     ORDER BY priority DESC,created_at
                     FOR UPDATE SKIP LOCKED LIMIT 1
                   )
                   UPDATE lh_background_jobs j SET
                     status='running',attempts=attempts+1,lease_owner=%s,locked_at=now(),
                     lease_expires_at=now()+(%s || ' seconds')::interval,updated_at=now()
                   FROM candidate WHERE j.id=candidate.id
                   RETURNING j.*""",
                (worker_id, max(15, min(int(lease_seconds), 3600))),
            ).fetchone()
        return self._job_dict(row) if row else None

    def complete_background_job(self, job_id: str, *, result: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_background_jobs SET status='succeeded',result=%s::jsonb,error=NULL,
                   completed_at=now(),updated_at=now() WHERE id=%s RETURNING *""",
                (_json(result), job_id),
            ).fetchone()
            if not row:
                raise KeyError("background job not found")
        return self._job_dict(row)

    def fail_background_job(self, job_id: str, *, error: str, retry_delay: float = 2) -> dict[str, Any]:
        with self._connect() as connection:
            current = connection.execute(
                "SELECT * FROM lh_background_jobs WHERE id=%s",
                (job_id,),
            ).fetchone()
            if not current:
                raise KeyError("background job not found")
            terminal = int(current["attempts"]) >= int(current["max_attempts"])
            row = connection.execute(
                """UPDATE lh_background_jobs SET status=%s,error=%s,
                   run_after=CASE WHEN %s THEN run_after ELSE now()+(%s || ' seconds')::interval END,
                   completed_at=CASE WHEN %s THEN now() ELSE NULL END,
                   lease_owner=NULL,locked_at=NULL,lease_expires_at=NULL,updated_at=now()
                   WHERE id=%s RETURNING *""",
                (
                    "dead" if terminal else "pending",
                    str(error),
                    terminal,
                    max(0.5, min(float(retry_delay), 3600.0)),
                    terminal,
                    job_id,
                ),
            ).fetchone()
        return self._job_dict(row)

    def status(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        clauses = []
        params: list[Any] = []
        if workspace_id:
            clauses.append("workspace_id=%s")
            params.append(workspace_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            jobs = connection.execute(
                f"""SELECT status,count(*) AS count FROM lh_background_jobs{where}
                    GROUP BY status""",
                params,
            ).fetchall()
            work = connection.execute(
                f"""SELECT status,count(*) AS count FROM lh_work_orders{where}
                    GROUP BY status""",
                params,
            ).fetchall()
        return {
            "background_jobs": {row["status"]: int(row["count"]) for row in jobs},
            "work_orders": {row["status"]: int(row["count"]) for row in work},
            "agents": self.list_agents(include_hidden=True),
        }

    @staticmethod
    def _agent_dict(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["id"] = str(value["id"])
        for key in ("created_at", "updated_at"):
            if value.get(key):
                value[key] = value[key].isoformat()
        return value

    @staticmethod
    def _work_dict(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["id"] = str(value["id"])
        for key in ("workspace_id", "parent_run_id", "agent_id"):
            if value.get(key):
                value[key] = str(value[key])
        for key in (
            "leased_at",
            "lease_expires_at",
            "heartbeat_at",
            "created_at",
            "updated_at",
            "completed_at",
        ):
            if value.get(key):
                value[key] = value[key].isoformat()
        return value

    @staticmethod
    def _job_dict(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        value["id"] = str(value["id"])
        for key in ("workspace_id", "conversation_id", "run_id", "work_order_id"):
            if value.get(key):
                value[key] = str(value[key])
        for key in (
            "run_after",
            "locked_at",
            "lease_expires_at",
            "created_at",
            "updated_at",
            "completed_at",
        ):
            if value.get(key):
                value[key] = value[key].isoformat()
        return value
