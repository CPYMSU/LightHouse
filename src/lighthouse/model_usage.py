from __future__ import annotations

import json
from typing import Any


class PostgresModelUsageStore:
    """Persist exact or explicitly estimated model usage for runs and Agents."""

    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("Model usage tracking requires psycopg") from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    def record(self, usage: dict[str, Any]) -> dict[str, Any]:
        input_tokens = max(0, int(usage.get("input_tokens") or 0))
        output_tokens = max(0, int(usage.get("output_tokens") or 0))
        cached_input_tokens = max(0, int(usage.get("cached_input_tokens") or 0))
        reasoning_tokens = max(0, int(usage.get("reasoning_tokens") or 0))
        total_tokens = max(
            0,
            int(usage.get("total_tokens") or input_tokens + output_tokens),
        )
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_model_usage(
                       workspace_id,conversation_id,run_id,work_order_id,agent_id,
                       project_id,provider,model,call_kind,input_tokens,output_tokens,
                       cached_input_tokens,reasoning_tokens,total_tokens,estimated,metadata
                   ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                   RETURNING *""",
                (
                    usage.get("workspace_id") or None,
                    usage.get("conversation_id") or None,
                    usage.get("run_id") or None,
                    usage.get("work_order_id") or None,
                    usage.get("agent_id") or None,
                    usage.get("project_id") or None,
                    str(usage.get("provider") or "openai-compatible"),
                    str(usage.get("model") or ""),
                    str(usage.get("call_kind") or "model"),
                    input_tokens,
                    output_tokens,
                    cached_input_tokens,
                    reasoning_tokens,
                    total_tokens,
                    bool(usage.get("estimated")),
                    json.dumps(usage.get("metadata") or {}, ensure_ascii=False, default=str),
                ),
            ).fetchone()
        return self._row(row)

    def summary(
        self,
        *,
        run_id: str | None = None,
        conversation_id: str | None = None,
        project_id: str | None = None,
        work_order_id: str | None = None,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        params: list[Any] = []
        for column, value in (
            ("run_id", run_id),
            ("conversation_id", conversation_id),
            ("project_id", project_id),
            ("work_order_id", work_order_id),
        ):
            if value:
                clauses.append(f"{column}=%s")
                params.append(value)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            total = connection.execute(
                f"""SELECT count(*) AS calls,
                            COALESCE(sum(input_tokens),0) AS input_tokens,
                            COALESCE(sum(output_tokens),0) AS output_tokens,
                            COALESCE(sum(cached_input_tokens),0) AS cached_input_tokens,
                            COALESCE(sum(reasoning_tokens),0) AS reasoning_tokens,
                            COALESCE(sum(total_tokens),0) AS total_tokens,
                            bool_or(estimated) AS contains_estimates
                     FROM lh_model_usage{where}""",
                params,
            ).fetchone()
            by_kind = connection.execute(
                f"""SELECT call_kind,count(*) AS calls,
                            COALESCE(sum(input_tokens),0) AS input_tokens,
                            COALESCE(sum(output_tokens),0) AS output_tokens,
                            COALESCE(sum(total_tokens),0) AS total_tokens,
                            bool_or(estimated) AS contains_estimates
                     FROM lh_model_usage{where}
                     GROUP BY call_kind ORDER BY total_tokens DESC""",
                params,
            ).fetchall()
            recent = connection.execute(
                f"""SELECT * FROM lh_model_usage{where}
                     ORDER BY created_at DESC LIMIT 30""",
                params,
            ).fetchall()
        return {
            "calls": int(total["calls"] or 0),
            "input_tokens": int(total["input_tokens"] or 0),
            "output_tokens": int(total["output_tokens"] or 0),
            "cached_input_tokens": int(total["cached_input_tokens"] or 0),
            "reasoning_tokens": int(total["reasoning_tokens"] or 0),
            "total_tokens": int(total["total_tokens"] or 0),
            "contains_estimates": bool(total["contains_estimates"]),
            "by_kind": [
                {
                    **dict(row),
                    "calls": int(row["calls"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "total_tokens": int(row["total_tokens"] or 0),
                    "contains_estimates": bool(row["contains_estimates"]),
                }
                for row in by_kind
            ],
            "recent": [self._row(row) for row in recent],
        }

    def run_and_conversation_summary(
        self,
        *,
        run_id: str,
        conversation_id: str | None,
    ) -> dict[str, Any]:
        return {
            "turn": self.summary(run_id=run_id),
            "conversation": (
                self.summary(conversation_id=conversation_id)
                if conversation_id
                else self.summary(run_id=run_id)
            ),
        }

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        value = dict(row)
        for key in (
            "workspace_id",
            "conversation_id",
            "run_id",
            "work_order_id",
            "agent_id",
            "project_id",
        ):
            if value.get(key) is not None:
                value[key] = str(value[key])
        if value.get("created_at"):
            value["created_at"] = value["created_at"].isoformat()
        return value
