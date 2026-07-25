from __future__ import annotations

from typing import Any

from .observable_agent_bus import ObservablePostgresAgentBus


class ScalablePostgresAgentBus(ObservablePostgresAgentBus):
    """Logical Work Orders scale freely; physical leases respect each Agent capacity."""

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
        }
