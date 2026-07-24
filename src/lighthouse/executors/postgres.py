from __future__ import annotations

import os
import re
from typing import Any

from ..models import Capability, ExecutionResult, Target, json_safe


_READ_PREFIX = re.compile(r"^\s*(select|show|with|explain|values)\b", re.IGNORECASE)


class PostgresExecutor:
    def _connect(self, target: Target):
        env_name = str(target.config.get("dsn_env") or "").strip()
        if not env_name:
            raise ValueError("data target requires config.dsn_env")
        dsn = os.environ.get(env_name)
        if not dsn:
            raise ValueError(f"data target secret environment variable is missing: {env_name}")
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL executor requires psycopg") from exc
        return psycopg.connect(dsn, row_factory=dict_row)

    def execute(self, capability: Capability, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if capability.operation == "schema":
            return self._schema(target, arguments)
        sql = str(arguments.get("sql") or "").strip()
        if not sql:
            raise ValueError("sql is required")
        params = arguments.get("params") or []
        if not isinstance(params, list):
            raise ValueError("params must be an array")
        if capability.operation == "query" and not _READ_PREFIX.match(sql):
            raise ValueError("data query accepts read-shaped SQL; use data exec for mutations")
        if capability.operation == "exec" and target.config.get("read_only") is True:
            raise PermissionError("data target is read-only")
        limit = max(1, min(int(arguments.get("limit") or 500), 5000))
        with self._connect(target) as connection:
            if capability.operation == "query":
                connection.execute("SET TRANSACTION READ ONLY")
            with connection.cursor() as cursor:
                cursor.execute(sql, params)
                columns = [item.name for item in cursor.description] if cursor.description else []
                rows = cursor.fetchmany(limit + 1) if cursor.description else []
                rowcount = cursor.rowcount
            if capability.operation == "query":
                connection.rollback()
            else:
                connection.commit()
        serialized = [json_safe(dict(row)) for row in rows[:limit]]
        return ExecutionResult(ok=True, result={"columns": columns, "rows": serialized, "rowcount": rowcount, "truncated": len(rows) > limit})

    def _schema(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        schema = str(arguments.get("schema") or "public")
        sql = """SELECT table_schema,table_name,column_name,data_type,is_nullable,ordinal_position
                 FROM information_schema.columns WHERE table_schema=%s
                 ORDER BY table_name,ordinal_position"""
        with self._connect(target) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            rows = connection.execute(sql, (schema,)).fetchall()
            connection.rollback()
        return ExecutionResult(ok=True, result={"schema": schema, "columns": [json_safe(dict(row)) for row in rows]})
