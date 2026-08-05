from __future__ import annotations

import json
from typing import Any, Mapping

from .warehouse_federation_protocol import canonical_json, payload_digest


class WarehouseFederationStore:
    """Durable local state for one outbound Warehouse federation connection."""

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
    def _public_run(row: Mapping[str, object]) -> dict[str, object]:
        return {
            "remote_run_id": str(row["remote_run_id"]),
            "local_run_id": str(row["local_run_id"]),
            "warehouse_origin": str(row["warehouse_origin"]),
            "workspace_id": str(row["workspace_id"]),
            "actor": str(row["actor"]),
            "conversation_ref": row.get("conversation_ref"),
            "policy": dict(row.get("policy") or {}),
            "status": str(row["status"]),
            "last_sent_sequence": int(row.get("last_sent_sequence") or 0),
            "result_digest": row.get("result_digest"),
            "last_error": row.get("last_error"),
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
        }

    def create_remote_run(
        self,
        *,
        remote_run_id: str,
        local_run_id: str,
        warehouse_origin: str,
        workspace_id: str,
        actor: str,
        conversation_ref: str | None,
        policy: Mapping[str, object],
    ) -> tuple[dict[str, object], bool]:
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO lh_warehouse_federation_runs(
                  remote_run_id,local_run_id,warehouse_origin,workspace_id,actor,
                  conversation_ref,policy,status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,'offered')
                ON CONFLICT (remote_run_id) DO NOTHING
                RETURNING *
                """,
                (
                    remote_run_id,
                    local_run_id,
                    warehouse_origin,
                    workspace_id,
                    actor,
                    conversation_ref,
                    json.dumps(dict(policy), ensure_ascii=False),
                ),
            ).fetchone()
            created = row is not None
            if row is None:
                row = connection.execute(
                    "SELECT * FROM lh_warehouse_federation_runs WHERE remote_run_id=%s",
                    (remote_run_id,),
                ).fetchone()
        return self._public_run(row), created

    def get_remote_run(self, remote_run_id: str) -> dict[str, object]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lh_warehouse_federation_runs WHERE remote_run_id=%s",
                (remote_run_id,),
            ).fetchone()
        if row is None:
            raise KeyError("Warehouse federation run not found")
        return self._public_run(row)

    def get_by_local_run(self, local_run_id: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM lh_warehouse_federation_runs WHERE local_run_id=%s",
                (local_run_id,),
            ).fetchone()
        return self._public_run(row) if row is not None else None

    def policy_for_local_run(self, local_run_id: str | None) -> dict[str, object] | None:
        if not local_run_id:
            return None
        run = self.get_by_local_run(local_run_id)
        if run is None:
            return None
        return {
            **dict(run["policy"]),
            "source": "warehouse_federation",
            "remote_run_id": run["remote_run_id"],
            "status": run["status"],
        }

    def update_run(
        self,
        remote_run_id: str,
        *,
        status: str | None = None,
        last_sent_sequence: int | None = None,
        result_digest: str | None = None,
        last_error: str | None = None,
    ) -> dict[str, object]:
        current = self.get_remote_run(remote_run_id)
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE lh_warehouse_federation_runs SET
                  status=%s,last_sent_sequence=%s,result_digest=%s,last_error=%s,
                  updated_at=now()
                WHERE remote_run_id=%s
                RETURNING *
                """,
                (
                    status or current["status"],
                    current["last_sent_sequence"]
                    if last_sent_sequence is None
                    else int(last_sent_sequence),
                    result_digest if result_digest is not None else current["result_digest"],
                    last_error if last_error is not None else current["last_error"],
                    remote_run_id,
                ),
            ).fetchone()
        return self._public_run(row)

    def list_active_runs(self) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM lh_warehouse_federation_runs
                WHERE status NOT IN ('completed','failed','cancelled','rejected')
                ORDER BY created_at
                """
            ).fetchall()
        return [self._public_run(row) for row in rows]

    def claim_incoming(self, envelope: Mapping[str, object]) -> tuple[bool, str]:
        message_id = str(envelope["message_id"])
        with self._connect() as connection:
            row = connection.execute(
                """
                INSERT INTO lh_warehouse_federation_messages(
                  direction,message_id,message_type,payload_hash,status
                ) VALUES ('inbound',%s,%s,%s,'processing')
                ON CONFLICT (direction,message_id) DO NOTHING
                RETURNING status
                """,
                (
                    message_id,
                    str(envelope["type"]),
                    payload_digest(envelope.get("payload") or {}),
                ),
            ).fetchone()
            if row is not None:
                return True, "processing"
            existing = connection.execute(
                """
                SELECT status,payload_hash FROM lh_warehouse_federation_messages
                WHERE direction='inbound' AND message_id=%s
                """,
                (message_id,),
            ).fetchone()
        if existing["payload_hash"] != payload_digest(envelope.get("payload") or {}):
            raise ValueError("message_id is already bound to another payload")
        return False, str(existing["status"])

    def finish_incoming(
        self,
        message_id: str,
        *,
        accepted: bool,
        error: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE lh_warehouse_federation_messages
                SET status=%s,error=%s,handled_at=now()
                WHERE direction='inbound' AND message_id=%s
                """,
                ("handled" if accepted else "rejected", error, message_id),
            )

    def enqueue_outbound(
        self,
        envelope: Mapping[str, object],
        *,
        remote_run_id: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO lh_warehouse_federation_outbox(
                  message_id,remote_run_id,message_type,envelope,status
                ) VALUES (%s,%s,%s,%s::jsonb,'pending')
                ON CONFLICT (message_id) DO NOTHING
                """,
                (
                    str(envelope["message_id"]),
                    remote_run_id,
                    str(envelope["type"]),
                    canonical_json(dict(envelope)),
                ),
            )

    def pending_outbox(self, *, limit: int = 500) -> list[dict[str, object]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT message_id,envelope FROM lh_warehouse_federation_outbox
                WHERE status='pending'
                ORDER BY created_at
                LIMIT %s
                """,
                (max(1, min(int(limit), 1000)),),
            ).fetchall()
        return [dict(row["envelope"]) for row in rows]

    def mark_outbound_attempt(self, message_id: str, error: str | None = None) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE lh_warehouse_federation_outbox
                SET attempt_count=attempt_count+1,last_attempt_at=now(),last_error=%s
                WHERE message_id=%s
                """,
                (error, message_id),
            )

    def mark_outbound_acknowledged(self, message_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE lh_warehouse_federation_outbox
                SET status='acknowledged',acknowledged_at=now(),last_error=NULL
                WHERE message_id=%s
                """,
                (message_id,),
            )

    def status(self) -> dict[str, object]:
        with self._connect() as connection:
            run_counts = connection.execute(
                "SELECT status,count(*) AS count FROM lh_warehouse_federation_runs GROUP BY status"
            ).fetchall()
            pending = connection.execute(
                "SELECT count(*) AS count FROM lh_warehouse_federation_outbox WHERE status='pending'"
            ).fetchone()
        return {
            "runs": {str(row["status"]): int(row["count"]) for row in run_counts},
            "pending_outbox": int(pending["count"]),
        }
