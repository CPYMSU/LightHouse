from __future__ import annotations

import json
from typing import Any
from uuid import uuid4


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


class PostgresMegaProjectStore:
    """Durable project knowledge without imposing a fixed project workflow."""

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Mega Project Store requires psycopg") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def create_project(
        self,
        *,
        workspace_id: str,
        title: str,
        goal: str,
        conversation_id: str | None = None,
        director_run_id: str | None = None,
        phase: str = "adaptive",
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        title = str(title or "").strip()
        goal = str(goal or "").strip()
        if not title or not goal:
            raise ValueError("project title and goal are required")
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_mega_projects(
                       id,workspace_id,conversation_id,director_run_id,title,goal,
                       status,current_phase,metadata
                   ) VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s::jsonb)
                   RETURNING *""",
                (
                    str(uuid4()),
                    workspace_id,
                    conversation_id,
                    director_run_id,
                    title,
                    goal,
                    str(phase or "adaptive"),
                    _json(metadata or {}),
                ),
            ).fetchone()
        return self._project_dict(row)

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lh_mega_projects WHERE id=%s",
                (project_id,),
            ).fetchone()
        if not row:
            raise KeyError("mega project not found")
        return self._project_dict(row)

    def active_project(
        self,
        *,
        workspace_id: str,
        conversation_id: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, Any] | None:
        clauses = ["workspace_id=%s", "status NOT IN ('completed','cancelled')"]
        params: list[Any] = [workspace_id]
        relation_clauses = []
        if conversation_id:
            relation_clauses.append("conversation_id=%s")
            params.append(conversation_id)
        if run_id:
            relation_clauses.append("director_run_id=%s")
            params.append(run_id)
        if relation_clauses:
            clauses.append("(" + " OR ".join(relation_clauses) + ")")
        with self._connect() as connection:
            row = connection.execute(
                f"""SELECT * FROM lh_mega_projects
                    WHERE {' AND '.join(clauses)}
                    ORDER BY updated_at DESC LIMIT 1""",
                params,
            ).fetchone()
        return self._project_dict(row) if row else None

    def inspect_project(self, project_id: str) -> dict[str, Any]:
        project = self.get_project(project_id)
        with self._connect() as connection:
            findings = connection.execute(
                """SELECT * FROM lh_project_findings
                   WHERE project_id=%s AND status='active'
                   ORDER BY confidence DESC,updated_at DESC LIMIT 80""",
                (project_id,),
            ).fetchall()
            steps = connection.execute(
                """SELECT * FROM lh_project_steps
                   WHERE project_id=%s ORDER BY sequence,created_at LIMIT 200""",
                (project_id,),
            ).fetchall()
            decisions = connection.execute(
                """SELECT * FROM lh_project_decisions
                   WHERE project_id=%s ORDER BY created_at DESC LIMIT 40""",
                (project_id,),
            ).fetchall()
            checkpoint = connection.execute(
                """SELECT * FROM lh_project_checkpoints
                   WHERE project_id=%s ORDER BY project_version DESC,created_at DESC LIMIT 1""",
                (project_id,),
            ).fetchone()
        return {
            "project": project,
            "findings": [self._finding_dict(row) for row in findings],
            "steps": [self._step_dict(row) for row in steps],
            "decisions": [self._decision_dict(row) for row in decisions],
            "latest_checkpoint": self._checkpoint_dict(checkpoint) if checkpoint else None,
            "workflow_enforced": False,
            "main_ai_decides_next_action": True,
        }

    def store_finding(
        self,
        *,
        project_id: str,
        finding_type: str,
        claim: str,
        domain: str = "general",
        confidence: float = 0.5,
        evidence: list[Any] | None = None,
        source_work_order_id: str | None = None,
        supersedes_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        claim = str(claim or "").strip()
        if not claim:
            raise ValueError("finding claim is required")
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_project_findings(
                       id,project_id,domain,finding_type,claim,confidence,evidence,
                       source_work_order_id,status,supersedes_id,metadata
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,'active',%s,%s::jsonb)
                   RETURNING *""",
                (
                    str(uuid4()),
                    project_id,
                    str(domain or "general"),
                    str(finding_type or "inference"),
                    claim,
                    max(0.0, min(float(confidence), 1.0)),
                    _json(evidence or []),
                    source_work_order_id,
                    supersedes_id,
                    _json(metadata or {}),
                ),
            ).fetchone()
            if supersedes_id:
                connection.execute(
                    """UPDATE lh_project_findings SET status='superseded',updated_at=now()
                       WHERE id=%s AND project_id=%s""",
                    (supersedes_id, project_id),
                )
            connection.execute(
                "UPDATE lh_mega_projects SET project_version=project_version+1,updated_at=now() WHERE id=%s",
                (project_id,),
            )
        return self._finding_dict(row)

    def search_findings(
        self,
        *,
        project_id: str,
        query: str = "",
        finding_types: list[str] | tuple[str, ...] = (),
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=%s", "status='active'"]
        params: list[Any] = [project_id]
        query = str(query or "").strip()
        if query:
            clauses.append(
                "(search_vector @@ plainto_tsquery('simple',%s) OR claim ILIKE %s OR domain ILIKE %s)"
            )
            params.extend([query, f"%{query}%", f"%{query}%"])
        if finding_types:
            clauses.append("finding_type = ANY(%s)")
            params.append(list(finding_types))
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM lh_project_findings
                    WHERE {' AND '.join(clauses)}
                    ORDER BY confidence DESC,updated_at DESC LIMIT %s""",
                params,
            ).fetchall()
        return [self._finding_dict(row) for row in rows]

    def create_step(
        self,
        *,
        project_id: str,
        title: str,
        goal: str,
        phase: str = "adaptive",
        status: str = "proposed",
        sequence: int = 0,
        parent_step_id: str | None = None,
        dependencies: list[Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not str(title or "").strip() or not str(goal or "").strip():
            raise ValueError("step title and goal are required")
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_project_steps(
                       id,project_id,parent_step_id,phase,title,goal,status,sequence,
                       dependencies,metadata
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                   RETURNING *""",
                (
                    str(uuid4()),
                    project_id,
                    parent_step_id,
                    str(phase or "adaptive"),
                    str(title).strip(),
                    str(goal).strip(),
                    str(status or "proposed"),
                    int(sequence),
                    _json(dependencies or []),
                    _json(metadata or {}),
                ),
            ).fetchone()
            connection.execute(
                "UPDATE lh_mega_projects SET project_version=project_version+1,updated_at=now() WHERE id=%s",
                (project_id,),
            )
        return self._step_dict(row)

    def update_step(
        self,
        step_id: str,
        *,
        status: str | None = None,
        assigned_work_order_id: str | None = None,
        implementation_receipts: list[Any] | None = None,
        verification: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        assignments = ["updated_at=now()"]
        params: list[Any] = []
        if status is not None:
            assignments.append("status=%s")
            params.append(status)
            if status == "completed":
                assignments.append("completed_at=now()")
        if assigned_work_order_id is not None:
            assignments.append("assigned_work_order_id=%s")
            params.append(assigned_work_order_id or None)
        if implementation_receipts is not None:
            assignments.append("implementation_receipts=%s::jsonb")
            params.append(_json(implementation_receipts))
        if verification is not None:
            assignments.append("verification=%s::jsonb")
            params.append(_json(verification))
        if metadata is not None:
            assignments.append("metadata=metadata || %s::jsonb")
            params.append(_json(metadata))
        params.append(step_id)
        with self._connect() as connection:
            row = connection.execute(
                f"UPDATE lh_project_steps SET {','.join(assignments)} WHERE id=%s RETURNING *",
                params,
            ).fetchone()
            if not row:
                raise KeyError("project step not found")
            connection.execute(
                "UPDATE lh_mega_projects SET project_version=project_version+1,updated_at=now() WHERE id=%s",
                (row["project_id"],),
            )
        return self._step_dict(row)

    def checkpoint(
        self,
        *,
        project_id: str,
        summary: str,
        phase: str = "adaptive",
        payload: dict[str, Any] | None = None,
        created_by: str = "main-ai",
    ) -> dict[str, Any]:
        with self._connect() as connection:
            project = connection.execute(
                "UPDATE lh_mega_projects SET current_phase=%s,project_version=project_version+1,updated_at=now() WHERE id=%s RETURNING *",
                (str(phase or "adaptive"), project_id),
            ).fetchone()
            if not project:
                raise KeyError("mega project not found")
            row = connection.execute(
                """INSERT INTO lh_project_checkpoints(
                       id,project_id,project_version,phase,summary,payload,created_by
                   ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING *""",
                (
                    str(uuid4()),
                    project_id,
                    int(project["project_version"]),
                    str(phase or "adaptive"),
                    str(summary or ""),
                    _json(payload or {}),
                    str(created_by or "main-ai"),
                ),
            ).fetchone()
        return self._checkpoint_dict(row)

    @staticmethod
    def _base_dict(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        for key in (
            "id",
            "workspace_id",
            "conversation_id",
            "director_run_id",
            "project_id",
            "source_work_order_id",
            "supersedes_id",
            "parent_step_id",
            "assigned_work_order_id",
        ):
            if value.get(key) is not None:
                value[key] = str(value[key])
        for key in ("created_at", "updated_at", "completed_at"):
            if value.get(key):
                value[key] = value[key].isoformat()
        value.pop("search_vector", None)
        return value

    @classmethod
    def _project_dict(cls, row: dict[str, Any]) -> dict[str, Any]:
        return cls._base_dict(row)

    @classmethod
    def _finding_dict(cls, row: dict[str, Any]) -> dict[str, Any]:
        value = cls._base_dict(row)
        value["confidence"] = float(value.get("confidence") or 0.0)
        return value

    @classmethod
    def _step_dict(cls, row: dict[str, Any]) -> dict[str, Any]:
        return cls._base_dict(row)

    @classmethod
    def _decision_dict(cls, row: dict[str, Any]) -> dict[str, Any]:
        return cls._base_dict(row)

    @classmethod
    def _checkpoint_dict(cls, row: dict[str, Any]) -> dict[str, Any]:
        return cls._base_dict(row)
