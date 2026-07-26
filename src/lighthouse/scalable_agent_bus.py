from __future__ import annotations

from typing import Any

from .agent_bus import _json
from .agent_coordination import merge_work_payload, prepare_work_order_payload
from .observable_agent_bus import ObservablePostgresAgentBus


_ACTIVE_WORK = ("queued", "leased", "running", "waiting_dependency", "waiting_confirmation")


class ScalablePostgresAgentBus(ObservablePostgresAgentBus):
    """Durable Agent Bus 2.0 with shared cognition and adaptive coordination."""

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
        prepared = prepare_work_order_payload(role, goal, payload)
        prepared["shared_findings"] = self.shared_findings(
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            limit=30,
        )
        coordination = prepared.get("coordination") or {}
        dedupe_key = str(coordination.get("dedupe_key") or "")
        with self._connect() as connection:
            existing = connection.execute(
                """SELECT * FROM lh_work_orders
                   WHERE workspace_id=%s AND role=%s
                     AND parent_run_id IS NOT DISTINCT FROM %s
                     AND status = ANY(%s)
                     AND payload #>> '{coordination,dedupe_key}'=%s
                   ORDER BY updated_at DESC LIMIT 1""",
                (workspace_id, role, parent_run_id, list(_ACTIVE_WORK), dedupe_key),
            ).fetchone()
            if existing:
                merged = merge_work_payload(dict(existing.get("payload") or {}), prepared)
                row = connection.execute(
                    """UPDATE lh_work_orders SET payload=%s::jsonb,
                         priority=GREATEST(priority,%s),updated_at=now()
                       WHERE id=%s RETURNING *""",
                    (_json(merged), max(0, min(int(priority), 100)), existing["id"]),
                ).fetchone()
                connection.execute(
                    """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                       VALUES (%s,'work_deduplicated',%s::jsonb)""",
                    (
                        row["id"],
                        _json(
                            {
                                "requested_by": requested_by,
                                "goal": goal,
                                "dedupe_key": dedupe_key,
                                "context_merged": True,
                            }
                        ),
                    ),
                )
                value = self._work_dict(row)
                value["deduplicated"] = True
                value["context_merged"] = True
                return value

        write_paths = list(((coordination.get("write_intent") or {}).get("paths") or []))
        conflicts = self._write_conflicts(
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            paths=write_paths,
        )
        if conflicts:
            coordination["conflicts"] = conflicts
            prepared["coordination"] = coordination
        work = super().dispatch(
            workspace_id=workspace_id,
            requested_by=requested_by,
            role=role,
            goal=goal,
            parent_run_id=parent_run_id,
            payload=prepared,
            priority=priority,
            visibility=visibility,
        )
        if conflicts:
            self.append_work_event(
                work["id"],
                "agent_conflict",
                {"kind": "write_intent", "conflicts": conflicts, "advisory_only": True},
            )
        work["deduplicated"] = False
        return work

    def claim_work_order(
        self,
        *,
        worker_id: str,
        execution_modes: tuple[str, ...] = ("model",),
        lease_seconds: int = 120,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE lh_work_orders SET status='queued',lease_owner=NULL,
                   leased_at=NULL,lease_expires_at=NULL,heartbeat_at=NULL,updated_at=now(),
                   error=COALESCE(error,'') || CASE WHEN error IS NULL OR error='' THEN '' ELSE E'\n' END ||
                         'Recovered after expired Agent lease'
                   WHERE status IN ('leased','running') AND lease_expires_at < now()"""
            )
            row = connection.execute(
                """WITH candidate AS (
                     SELECT wo.id FROM lh_work_orders wo
                     JOIN lh_agents a ON a.id=wo.agent_id
                     WHERE wo.status='queued'
                       AND a.execution_mode = ANY(%s)
                       AND (
                         SELECT count(*) FROM lh_work_orders active
                         WHERE active.agent_id=wo.agent_id
                           AND active.status IN ('leased','running','waiting_confirmation')
                           AND (active.lease_expires_at IS NULL OR active.lease_expires_at > now())
                       ) < a.max_concurrency
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
                       VALUES (%s,'work_leased',jsonb_build_object('worker_id',%s))""",
                    (row["id"], worker_id),
                )
        return self._work_dict(row) if row else None

    def update_work_payload(self, work_order_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_work_order(work_order_id)
        merged = merge_work_payload(dict(current.get("payload") or {}), dict(patch or {}))
        with self._connect() as connection:
            row = connection.execute(
                "UPDATE lh_work_orders SET payload=%s::jsonb,updated_at=now() WHERE id=%s RETURNING *",
                (_json(merged), work_order_id),
            ).fetchone()
        if not row:
            raise KeyError("work order not found")
        return self._work_dict(row)

    def add_dependencies(self, work_order_id: str, depends_on_ids: list[str]) -> dict[str, Any]:
        ids = [str(item) for item in depends_on_ids if str(item) and str(item) != str(work_order_id)]
        if not ids:
            return self.get_work_order(work_order_id)
        with self._connect() as connection:
            for dependency_id in ids:
                connection.execute(
                    """INSERT INTO lh_work_dependencies(work_order_id,depends_on_id)
                       VALUES (%s,%s) ON CONFLICT DO NOTHING""",
                    (work_order_id, dependency_id),
                )
            connection.execute(
                """INSERT INTO lh_work_events(work_order_id,event_type,payload)
                   VALUES (%s,'work_dependencies_updated',%s::jsonb)""",
                (work_order_id, _json({"depends_on": ids})),
            )
        return self.get_work_order(work_order_id)

    def acquire_write_intent(self, work_order_id: str, paths: list[str]) -> dict[str, Any]:
        work = self.get_work_order(work_order_id)
        unique = sorted({str(item).strip() for item in paths if str(item).strip()})
        conflicts = self._write_conflicts(
            workspace_id=work["workspace_id"],
            parent_run_id=work.get("parent_run_id"),
            paths=unique,
            exclude_work_order_id=work_order_id,
        )
        coordination = dict((work.get("payload") or {}).get("coordination") or {})
        coordination["write_intent"] = {
            "paths": unique,
            "mode": "modify" if unique else "none",
            "status": "active" if unique else "not_required",
            "conflicts": conflicts,
        }
        if conflicts:
            coordination["conflicts"] = [
                *list(coordination.get("conflicts") or []),
                *conflicts,
            ]
        updated = self.update_work_payload(work_order_id, {"coordination": coordination})
        self.append_work_event(
            work_order_id,
            "write_intent_acquired",
            {"paths": unique, "conflicts": conflicts, "advisory_only": True},
        )
        if conflicts:
            self.append_work_event(
                work_order_id,
                "agent_conflict",
                {"kind": "write_intent", "conflicts": conflicts, "advisory_only": True},
            )
        return updated

    def publish_findings(self, work_order_id: str, findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        published: list[dict[str, Any]] = []
        for item in findings[:60]:
            if not isinstance(item, dict) or not str(item.get("claim") or "").strip():
                continue
            published.append(self.append_work_event(work_order_id, "finding_published", item))
        return published

    def shared_findings(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT e.id,e.work_order_id,e.payload,e.created_at,w.role
                   FROM lh_work_events e JOIN lh_work_orders w ON w.id=e.work_order_id
                   WHERE w.workspace_id=%s
                     AND w.parent_run_id IS NOT DISTINCT FROM %s
                     AND e.event_type='finding_published'
                   ORDER BY e.id DESC LIMIT %s""",
                (workspace_id, parent_run_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "work_order_id": str(row["work_order_id"]),
                "source_agent": row["role"],
                **dict(row["payload"] or {}),
                "created_at": row["created_at"].isoformat(),
            }
            for row in reversed(rows)
        ]

    def active_conflicts(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT e.id,e.work_order_id,e.payload,e.created_at,w.role
                   FROM lh_work_events e JOIN lh_work_orders w ON w.id=e.work_order_id
                   WHERE w.workspace_id=%s
                     AND w.parent_run_id IS NOT DISTINCT FROM %s
                     AND e.event_type='agent_conflict'
                   ORDER BY e.id DESC LIMIT %s""",
                (workspace_id, parent_run_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "work_order_id": str(row["work_order_id"]),
                "role": row["role"],
                "payload": row["payload"],
                "created_at": row["created_at"].isoformat(),
            }
            for row in rows
        ]

    def resource_advice(self, *, workspace_id: str | None = None) -> dict[str, Any]:
        clauses = []
        params: list[Any] = []
        if workspace_id:
            clauses.append("w.workspace_id=%s")
            params.append(workspace_id)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = connection.execute(
                f"""SELECT a.role,a.max_concurrency,
                            count(*) FILTER (WHERE w.status IN ('leased','running','waiting_confirmation')) AS active,
                            count(*) FILTER (WHERE w.status='queued') AS queued
                     FROM lh_agents a LEFT JOIN lh_work_orders w ON w.agent_id=a.id
                     {where}
                     GROUP BY a.id,a.role,a.max_concurrency ORDER BY a.role""",
                params,
            ).fetchall()
        roles = []
        for row in rows:
            roles.append(
                {
                    "role": row["role"],
                    "max_concurrency": int(row["max_concurrency"]),
                    "active": int(row["active"] or 0),
                    "queued": int(row["queued"] or 0),
                    "available_slots": max(0, int(row["max_concurrency"]) - int(row["active"] or 0)),
                }
            )
        return {
            "roles": roles,
            "total_active": sum(item["active"] for item in roles),
            "total_queued": sum(item["queued"] for item in roles),
            "recommended_action": (
                "distill_or_cancel_duplicate_work"
                if sum(item["queued"] for item in roles) > 100
                else "adaptive_queue_healthy"
            ),
            "advisory_only": True,
            "logical_work_order_count_has_no_product_limit": True,
            "agent_bus_version": "2.0",
        }

    def _write_conflicts(
        self,
        *,
        workspace_id: str,
        parent_run_id: str | None,
        paths: list[str],
        exclude_work_order_id: str | None = None,
    ) -> list[dict[str, Any]]:
        wanted = {str(item).strip() for item in paths if str(item).strip()}
        if not wanted:
            return []
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id,role,payload FROM lh_work_orders
                   WHERE workspace_id=%s
                     AND parent_run_id IS NOT DISTINCT FROM %s
                     AND status = ANY(%s)
                   ORDER BY updated_at DESC LIMIT 200""",
                (workspace_id, parent_run_id, list(_ACTIVE_WORK)),
            ).fetchall()
        conflicts: list[dict[str, Any]] = []
        for row in rows:
            if exclude_work_order_id and str(row["id"]) == str(exclude_work_order_id):
                continue
            payload = dict(row.get("payload") or {})
            coordination = payload.get("coordination") if isinstance(payload.get("coordination"), dict) else {}
            intent = coordination.get("write_intent") if isinstance(coordination.get("write_intent"), dict) else {}
            existing = {str(item) for item in intent.get("paths") or []}
            overlap = sorted(wanted.intersection(existing))
            if overlap:
                conflicts.append(
                    {
                        "subject": "overlapping_write_intent",
                        "with_work_order_id": str(row["id"]),
                        "with_role": row["role"],
                        "paths": overlap,
                        "severity": "important",
                        "requires": "main_ai_or_integration_review",
                    }
                )
        return conflicts
