from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any
from uuid import uuid4


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class PostgresMassiveBuildStore:
    """Durable Build Cells, contracts, leases, batches and integrations.

    The store records reality and coordination state. It deliberately does not
    enforce a project phase order; the main AI remains the Project Director.
    """

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Massive Build Store requires psycopg") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def create_cell(
        self,
        *,
        project_id: str,
        name: str,
        goal: str,
        domain: str = "general",
        strategy: str = "adaptive",
        base_commit: str | None = None,
        contract_ids: list[Any] | None = None,
        dependencies: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(name or "").strip() or not str(goal or "").strip():
            raise ValueError("cell name and goal are required")
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_project_cells(
                       id,project_id,name,domain,goal,status,strategy,base_commit,
                       contract_ids,dependencies,metadata
                   ) VALUES (%s,%s,%s,%s,%s,'ready',%s,%s,%s::jsonb,%s::jsonb,%s::jsonb)
                   RETURNING *""",
                (
                    str(uuid4()),
                    project_id,
                    str(name).strip(),
                    str(domain or "general"),
                    str(goal).strip(),
                    str(strategy or "adaptive"),
                    base_commit,
                    _json(contract_ids or []),
                    _json(dependencies or []),
                    _json(metadata or {}),
                ),
            ).fetchone()
            self._touch_project(connection, project_id)
        return self._row(row)

    def update_cell(
        self,
        cell_id: str,
        *,
        status: str | None = None,
        progress: float | None = None,
        worktree_id: str | None = None,
        assigned_work_orders: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assignments = ["updated_at=now()"]
        params: list[Any] = []
        if status is not None:
            assignments.append("status=%s")
            params.append(str(status))
            if status == "completed":
                assignments.append("completed_at=now()")
        if progress is not None:
            assignments.append("progress=%s")
            params.append(max(0.0, min(float(progress), 1.0)))
        if worktree_id is not None:
            assignments.append("worktree_id=%s")
            params.append(worktree_id or None)
        if assigned_work_orders is not None:
            assignments.append("assigned_work_orders=%s::jsonb")
            params.append(_json(assigned_work_orders))
        if metadata is not None:
            assignments.append("metadata=metadata || %s::jsonb")
            params.append(_json(metadata))
        params.append(cell_id)
        with self._connect() as connection:
            row = connection.execute(
                f"UPDATE lh_project_cells SET {','.join(assignments)} WHERE id=%s RETURNING *",
                params,
            ).fetchone()
            if not row:
                raise KeyError("build cell not found")
            self._touch_project(connection, str(row["project_id"]))
        return self._row(row)

    def create_contract(
        self,
        *,
        project_id: str,
        contract_type: str,
        name: str,
        schema: dict[str, Any],
        status: str = "draft",
        owner: str = "main-ai",
        consumers: list[Any] | None = None,
        evidence: list[Any] | None = None,
        supersedes_id: str | None = None,
    ) -> dict[str, Any]:
        if not str(contract_type or "").strip() or not str(name or "").strip():
            raise ValueError("contract type and name are required")
        with self._connect() as connection:
            latest = connection.execute(
                """SELECT COALESCE(max(version),0) AS version FROM lh_project_contracts
                   WHERE project_id=%s AND contract_type=%s AND name=%s""",
                (project_id, contract_type, name),
            ).fetchone()
            version = int(latest["version"] or 0) + 1
            row = connection.execute(
                """INSERT INTO lh_project_contracts(
                       id,project_id,contract_type,name,version,status,schema,owner,
                       consumers,evidence,supersedes_id
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,%s::jsonb,%s)
                   RETURNING *""",
                (
                    str(uuid4()),
                    project_id,
                    str(contract_type),
                    str(name),
                    version,
                    str(status or "draft"),
                    _json(schema or {}),
                    str(owner or "main-ai"),
                    _json(consumers or []),
                    _json(evidence or []),
                    supersedes_id,
                ),
            ).fetchone()
            if supersedes_id:
                connection.execute(
                    "UPDATE lh_project_contracts SET status='superseded',updated_at=now() WHERE id=%s",
                    (supersedes_id,),
                )
            self._touch_project(connection, project_id)
        return self._row(row)

    def list_contracts(self, project_id: str, *, include_deprecated: bool = False) -> list[dict[str, Any]]:
        clause = "" if include_deprecated else "AND status NOT IN ('deprecated','superseded')"
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM lh_project_contracts WHERE project_id=%s {clause}
                    ORDER BY contract_type,name,version DESC""",
                (project_id,),
            ).fetchall()
        return [self._row(row) for row in rows]

    def acquire_lease(
        self,
        *,
        project_id: str,
        scope_type: str,
        scope: str,
        cell_id: str | None = None,
        owner_work_order_id: str | None = None,
        base_commit: str | None = None,
        lease_seconds: int = 1800,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        scope = str(scope or "").strip()
        if not scope:
            raise ValueError("write lease scope is required")
        expires = datetime.now(timezone.utc) + timedelta(seconds=max(60, min(int(lease_seconds), 86400)))
        with self._connect() as connection:
            conflict = connection.execute(
                """SELECT * FROM lh_project_write_leases
                   WHERE project_id=%s AND status='active' AND expires_at > now()
                     AND (scope=%s OR scope LIKE %s OR %s LIKE scope || '%%')
                     AND owner_work_order_id IS DISTINCT FROM %s
                   ORDER BY acquired_at LIMIT 1 FOR UPDATE""",
                (project_id, scope, scope.rstrip("/") + "/%", scope, owner_work_order_id),
            ).fetchone()
            if conflict:
                raise ValueError(
                    "write scope is already leased by work order "
                    + str(conflict.get("owner_work_order_id") or "another build cell")
                )
            row = connection.execute(
                """INSERT INTO lh_project_write_leases(
                       id,project_id,cell_id,owner_work_order_id,scope_type,scope,mode,
                       status,base_commit,metadata,expires_at
                   ) VALUES (%s,%s,%s,%s,%s,%s,'write','active',%s,%s::jsonb,%s)
                   RETURNING *""",
                (
                    str(uuid4()),
                    project_id,
                    cell_id,
                    owner_work_order_id,
                    str(scope_type or "path"),
                    scope,
                    base_commit,
                    _json(metadata or {}),
                    expires,
                ),
            ).fetchone()
        return self._row(row)

    def release_lease(self, lease_id: str, *, status: str = "released") -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """UPDATE lh_project_write_leases SET status=%s,released_at=now()
                   WHERE id=%s AND status='active' RETURNING *""",
                (status, lease_id),
            ).fetchone()
            if not row:
                row = connection.execute(
                    "SELECT * FROM lh_project_write_leases WHERE id=%s",
                    (lease_id,),
                ).fetchone()
        if not row:
            raise KeyError("write lease not found")
        return self._row(row)

    def valid_lease(
        self,
        *,
        project_id: str,
        owner_work_order_id: str,
        path: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["project_id=%s", "owner_work_order_id=%s", "status='active'", "expires_at > now()"]
        params: list[Any] = [project_id, owner_work_order_id]
        if path:
            clauses.append("(%s=scope OR %s LIKE scope || '/%%' OR scope='.')")
            params.extend([path, path])
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT * FROM lh_project_write_leases WHERE {' AND '.join(clauses)} ORDER BY acquired_at DESC LIMIT 1",
                params,
            ).fetchone()
        return self._row(row) if row else None

    def register_worktree(
        self,
        *,
        project_id: str,
        path: str,
        branch: str,
        base_ref: str,
        cell_id: str | None = None,
        head_commit: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_project_worktrees(
                       id,project_id,cell_id,path,branch,base_ref,status,head_commit,metadata
                   ) VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s::jsonb)
                   ON CONFLICT (project_id,path) DO UPDATE SET
                     cell_id=EXCLUDED.cell_id,branch=EXCLUDED.branch,base_ref=EXCLUDED.base_ref,
                     status='active',head_commit=EXCLUDED.head_commit,metadata=lh_project_worktrees.metadata || EXCLUDED.metadata,
                     updated_at=now()
                   RETURNING *""",
                (
                    str(uuid4()), project_id, cell_id, path, branch, base_ref,
                    head_commit, _json(metadata or {}),
                ),
            ).fetchone()
            if cell_id:
                connection.execute(
                    "UPDATE lh_project_cells SET worktree_id=%s,updated_at=now() WHERE id=%s",
                    (row["id"], cell_id),
                )
        return self._row(row)

    def update_worktree(
        self,
        worktree_id: str,
        *,
        status: str | None = None,
        head_commit: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assignments = ["updated_at=now()"]
        params: list[Any] = []
        if status is not None:
            assignments.append("status=%s")
            params.append(status)
        if head_commit is not None:
            assignments.append("head_commit=%s")
            params.append(head_commit)
        if metadata is not None:
            assignments.append("metadata=metadata || %s::jsonb")
            params.append(_json(metadata))
        params.append(worktree_id)
        with self._connect() as connection:
            row = connection.execute(
                f"UPDATE lh_project_worktrees SET {','.join(assignments)} WHERE id=%s RETURNING *",
                params,
            ).fetchone()
        if not row:
            raise KeyError("project worktree not found")
        return self._row(row)

    def create_batch(
        self,
        *,
        project_id: str,
        title: str,
        goal: str,
        cell_id: str | None = None,
        base_commit: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_project_batches(
                       id,project_id,cell_id,title,goal,status,base_commit,metadata
                   ) VALUES (%s,%s,%s,%s,%s,'running',%s,%s::jsonb) RETURNING *""",
                (
                    str(uuid4()), project_id, cell_id, str(title), str(goal),
                    base_commit, _json(metadata or {}),
                ),
            ).fetchone()
        return self._row(row)

    def update_batch(self, batch_id: str, **values: Any) -> dict[str, Any]:
        allowed = {
            "status", "head_commit", "changed_files", "added_lines", "deleted_lines",
            "diff_summary", "receipts", "verification", "metadata",
        }
        assignments = ["updated_at=now()"]
        params: list[Any] = []
        for key, value in values.items():
            if key not in allowed or value is None:
                continue
            if key in {"changed_files", "diff_summary", "receipts", "verification", "metadata"}:
                if key == "metadata":
                    assignments.append("metadata=metadata || %s::jsonb")
                else:
                    assignments.append(f"{key}=%s::jsonb")
                params.append(_json(value))
            else:
                assignments.append(f"{key}=%s")
                params.append(value)
        if values.get("status") in {"accepted", "rolled_back", "failed", "cancelled"}:
            assignments.append("completed_at=now()")
        params.append(batch_id)
        with self._connect() as connection:
            row = connection.execute(
                f"UPDATE lh_project_batches SET {','.join(assignments)} WHERE id=%s RETURNING *",
                params,
            ).fetchone()
        if not row:
            raise KeyError("project batch not found")
        return self._row(row)

    def create_integration(
        self,
        *,
        project_id: str,
        title: str,
        scope: str = "project",
        source_cells: list[Any] | None = None,
        source_batches: list[Any] | None = None,
        base_commit: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_project_integrations(
                       id,project_id,title,scope,status,source_cells,source_batches,
                       base_commit,metadata
                   ) VALUES (%s,%s,%s,%s,'proposed',%s::jsonb,%s::jsonb,%s,%s::jsonb)
                   RETURNING *""",
                (
                    str(uuid4()), project_id, str(title), str(scope or "project"),
                    _json(source_cells or []), _json(source_batches or []),
                    base_commit, _json(metadata or {}),
                ),
            ).fetchone()
        return self._row(row)

    def update_integration(self, integration_id: str, **values: Any) -> dict[str, Any]:
        allowed = {"status", "result_commit", "conflicts", "receipts", "verification", "metadata"}
        assignments = ["updated_at=now()"]
        params: list[Any] = []
        for key, value in values.items():
            if key not in allowed or value is None:
                continue
            if key in {"conflicts", "receipts", "verification", "metadata"}:
                assignments.append(
                    "metadata=metadata || %s::jsonb" if key == "metadata" else f"{key}=%s::jsonb"
                )
                params.append(_json(value))
            else:
                assignments.append(f"{key}=%s")
                params.append(value)
        if values.get("status") in {"succeeded", "rolled_back", "failed", "cancelled"}:
            assignments.append("completed_at=now()")
        params.append(integration_id)
        with self._connect() as connection:
            row = connection.execute(
                f"UPDATE lh_project_integrations SET {','.join(assignments)} WHERE id=%s RETURNING *",
                params,
            ).fetchone()
        if not row:
            raise KeyError("project integration not found")
        return self._row(row)

    def upsert_wiring(
        self,
        *,
        project_id: str,
        feature_key: str,
        title: str,
        states: dict[str, Any],
        evidence: list[Any] | None = None,
        work_order_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        keys = (
            "frontend_state", "event_state", "api_state", "service_state",
            "repository_state", "database_state", "receipt_state", "e2e_state",
            "overall_state",
        )
        values = {key: str(states.get(key) or "unknown") for key in keys}
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_feature_wiring(
                       id,project_id,feature_key,title,frontend_state,event_state,api_state,
                       service_state,repository_state,database_state,receipt_state,e2e_state,
                       overall_state,evidence,metadata,verified_by_work_order_id
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                   ON CONFLICT (project_id,feature_key) DO UPDATE SET
                     title=EXCLUDED.title,frontend_state=EXCLUDED.frontend_state,
                     event_state=EXCLUDED.event_state,api_state=EXCLUDED.api_state,
                     service_state=EXCLUDED.service_state,repository_state=EXCLUDED.repository_state,
                     database_state=EXCLUDED.database_state,receipt_state=EXCLUDED.receipt_state,
                     e2e_state=EXCLUDED.e2e_state,overall_state=EXCLUDED.overall_state,
                     evidence=EXCLUDED.evidence,metadata=lh_feature_wiring.metadata || EXCLUDED.metadata,
                     verified_by_work_order_id=EXCLUDED.verified_by_work_order_id,updated_at=now()
                   RETURNING *""",
                (
                    str(uuid4()), project_id, str(feature_key), str(title),
                    values["frontend_state"], values["event_state"], values["api_state"],
                    values["service_state"], values["repository_state"], values["database_state"],
                    values["receipt_state"], values["e2e_state"], values["overall_state"],
                    _json(evidence or []), _json(metadata or {}), work_order_id,
                ),
            ).fetchone()
        return self._row(row)

    def project_brief(self, project_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            cells = connection.execute(
                "SELECT * FROM lh_project_cells WHERE project_id=%s ORDER BY updated_at DESC LIMIT 100",
                (project_id,),
            ).fetchall()
            leases = connection.execute(
                """SELECT * FROM lh_project_write_leases
                   WHERE project_id=%s AND status='active' AND expires_at > now()
                   ORDER BY acquired_at DESC""",
                (project_id,),
            ).fetchall()
            batches = connection.execute(
                "SELECT * FROM lh_project_batches WHERE project_id=%s ORDER BY updated_at DESC LIMIT 100",
                (project_id,),
            ).fetchall()
            integrations = connection.execute(
                "SELECT * FROM lh_project_integrations WHERE project_id=%s ORDER BY updated_at DESC LIMIT 40",
                (project_id,),
            ).fetchall()
            worktrees = connection.execute(
                "SELECT * FROM lh_project_worktrees WHERE project_id=%s ORDER BY updated_at DESC LIMIT 100",
                (project_id,),
            ).fetchall()
            wiring = connection.execute(
                "SELECT * FROM lh_feature_wiring WHERE project_id=%s ORDER BY updated_at DESC LIMIT 100",
                (project_id,),
            ).fetchall()
        return {
            "cells": [self._row(row) for row in cells],
            "contracts": self.list_contracts(project_id),
            "active_write_leases": [self._row(row) for row in leases],
            "batches": [self._row(row) for row in batches],
            "integrations": [self._row(row) for row in integrations],
            "worktrees": [self._row(row) for row in worktrees],
            "wiring": [self._row(row) for row in wiring],
            "workflow_enforced": False,
            "main_ai_decides_waiting_and_next_action": True,
        }

    @staticmethod
    def _touch_project(connection, project_id: str) -> None:
        connection.execute(
            "UPDATE lh_mega_projects SET project_version=project_version+1,updated_at=now() WHERE id=%s",
            (project_id,),
        )

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        for key, item in list(value.items()):
            if item is None:
                continue
            if key == "id" or key.endswith("_id"):
                value[key] = str(item)
            elif hasattr(item, "isoformat"):
                value[key] = item.isoformat()
        return value
