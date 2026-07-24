from __future__ import annotations

import os
import re
from typing import Any

from ..data_kernel import DataCatalog
from ..models import Capability, ExecutionResult, Target, json_safe

_READ_PREFIX = re.compile(r"^\s*(select|show|with|explain|values)\b", re.IGNORECASE)
_FILTER_SUFFIX = {"eq", "in", "contains", "gte", "lte", "gt", "lt", "isnull"}


class PostgresExecutor:
    def __init__(self, catalog: DataCatalog | None = None):
        self.catalog = catalog

    def _driver(self):
        try:
            import psycopg
            from psycopg import sql
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError("PostgreSQL executor requires psycopg") from exc
        return psycopg, sql, dict_row

    def _connect(self, target: Target):
        env_name = str(target.config.get("dsn_env") or "").strip()
        if not env_name:
            raise ValueError("data target requires config.dsn_env")
        dsn = os.environ.get(env_name)
        if not dsn:
            raise ValueError(f"data target secret environment variable is missing: {env_name}")
        psycopg, _sql, dict_row = self._driver()
        return psycopg.connect(dsn, row_factory=dict_row)

    def execute(self, capability: Capability, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        operation = capability.operation
        if operation == "schema":
            return self._schema(target, arguments)
        if operation == "catalog_sync":
            return self._catalog_sync(target, arguments)
        if operation == "catalog_resources":
            return self._catalog_resources(target, arguments)
        if operation == "catalog_bind":
            return self._catalog_bind(target, arguments)
        if operation == "resource_policy":
            return self._resource_policy(target, arguments)
        if operation == "semantic_register":
            return self._semantic_register(target, arguments)
        if operation == "semantic_list":
            return self._semantic_list(target, arguments)
        if operation == "resource_list":
            return self._resource_search(target, arguments)
        if operation == "resource_show":
            return self._resource_show(target, arguments)
        if operation == "resource_search":
            return self._resource_search(target, arguments)
        if operation == "resource_update":
            return self._resource_update(target, arguments)
        if operation == "semantic_query":
            return self._semantic_query(target, arguments)
        return self._raw_sql(capability, target, arguments)

    def _raw_sql(self, capability: Capability, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        sql_text = str(arguments.get("sql") or "").strip()
        if not sql_text:
            raise ValueError("sql is required")
        params = arguments.get("params") or []
        if not isinstance(params, list):
            raise ValueError("params must be an array")
        if capability.operation == "query":
            if target.config.get("raw_sql_query", True) is not True:
                raise PermissionError("raw SQL query is disabled for this data target")
            if not _READ_PREFIX.match(sql_text):
                raise ValueError("data query accepts read-shaped SQL; use data exec for mutations")
        if capability.operation == "exec":
            if target.config.get("read_only") is True or target.config.get("raw_sql_exec", True) is not True:
                raise PermissionError("raw SQL mutation is disabled for this data target")
        limit = self._limit(target, arguments)
        with self._connect(target) as connection:
            if capability.operation == "query":
                connection.execute("SET TRANSACTION READ ONLY")
            with connection.cursor() as cursor:
                cursor.execute(sql_text, params)
                columns = [item.name for item in cursor.description] if cursor.description else []
                rows = cursor.fetchmany(limit + 1) if cursor.description else []
                rowcount = cursor.rowcount
            if capability.operation == "query":
                connection.rollback()
            else:
                connection.commit()
        serialized = [json_safe(dict(row)) for row in rows[:limit]]
        return ExecutionResult(ok=True, result={"columns": columns, "rows": serialized, "rowcount": rowcount, "truncated": len(rows) > limit, "target_id": target.id})

    def _limit(self, target: Target, arguments: dict[str, Any], *, policy_limit: int | None = None) -> int:
        maximum = int(target.config.get("max_rows") or 5000)
        if policy_limit:
            maximum = min(maximum, int(policy_limit))
        return max(1, min(int(arguments.get("limit") or min(500, maximum)), maximum, 5000))

    def _schemas(self, target: Target) -> tuple[list[str], list[str]]:
        allowed = [str(item) for item in target.config.get("allowed_schemas") or []]
        excluded = [str(item) for item in target.config.get("excluded_schemas") or ["pg_catalog", "information_schema"]]
        return allowed, excluded

    def _schema(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        schema = str(arguments.get("schema") or "public")
        allowed, excluded = self._schemas(target)
        if allowed and schema not in allowed:
            raise PermissionError("schema is outside the data target allowlist")
        if schema in excluded:
            raise PermissionError("schema is excluded by data target policy")
        query = """SELECT table_schema,table_name,column_name,data_type,is_nullable,ordinal_position
                   FROM information_schema.columns WHERE table_schema=%s
                   ORDER BY table_name,ordinal_position"""
        with self._connect(target) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            rows = connection.execute(query, (schema,)).fetchall()
            connection.rollback()
        return ExecutionResult(ok=True, result={"schema": schema, "columns": [json_safe(dict(row)) for row in rows], "target_id": target.id})

    def _catalog_snapshot(self, target: Target) -> dict[str, Any]:
        allowed, excluded = self._schemas(target)
        with self._connect(target) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            database = connection.execute("SELECT current_database() AS name").fetchone()["name"]
            columns = connection.execute(
                """SELECT c.table_schema,c.table_name,t.table_type,c.column_name,c.data_type,
                          c.udt_name,c.is_nullable,c.column_default,c.ordinal_position
                   FROM information_schema.columns c
                   JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name
                   WHERE (%s::text[] = '{}'::text[] OR c.table_schema=ANY(%s))
                     AND NOT (c.table_schema=ANY(%s))
                   ORDER BY c.table_schema,c.table_name,c.ordinal_position""",
                (allowed, allowed, excluded),
            ).fetchall()
            primary_keys = connection.execute(
                """SELECT n.nspname AS table_schema,cl.relname AS table_name,a.attname AS column_name,
                          array_position(i.indkey::smallint[],a.attnum::smallint) AS position
                   FROM pg_index i JOIN pg_class cl ON cl.oid=i.indrelid
                   JOIN pg_namespace n ON n.oid=cl.relnamespace
                   JOIN pg_attribute a ON a.attrelid=cl.oid AND a.attnum=ANY(i.indkey)
                   WHERE i.indisprimary AND (%s::text[]='{}'::text[] OR n.nspname=ANY(%s))
                     AND NOT (n.nspname=ANY(%s))
                   ORDER BY n.nspname,cl.relname,position""",
                (allowed, allowed, excluded),
            ).fetchall()
            foreign_keys = connection.execute(
                """SELECT src_ns.nspname AS source_schema,src.relname AS source_table,
                          src_a.attname AS source_column,dst_ns.nspname AS target_schema,
                          dst.relname AS target_table,dst_a.attname AS target_column,
                          con.conname AS constraint_name
                   FROM pg_constraint con
                   JOIN pg_class src ON src.oid=con.conrelid JOIN pg_namespace src_ns ON src_ns.oid=src.relnamespace
                   JOIN pg_class dst ON dst.oid=con.confrelid JOIN pg_namespace dst_ns ON dst_ns.oid=dst.relnamespace
                   JOIN LATERAL unnest(con.conkey) WITH ORDINALITY sk(attnum,ord) ON TRUE
                   JOIN LATERAL unnest(con.confkey) WITH ORDINALITY dk(attnum,ord) ON dk.ord=sk.ord
                   JOIN pg_attribute src_a ON src_a.attrelid=src.oid AND src_a.attnum=sk.attnum
                   JOIN pg_attribute dst_a ON dst_a.attrelid=dst.oid AND dst_a.attnum=dk.attnum
                   WHERE con.contype='f' AND (%s::text[]='{}'::text[] OR src_ns.nspname=ANY(%s))
                     AND NOT (src_ns.nspname=ANY(%s))
                   ORDER BY source_schema,source_table,constraint_name,sk.ord""",
                (allowed, allowed, excluded),
            ).fetchall()
            connection.rollback()
        pk_map: dict[tuple[str, str], list[str]] = {}
        for row in primary_keys:
            pk_map.setdefault((row["table_schema"], row["table_name"]), []).append(row["column_name"])
        table_map: dict[tuple[str, str], dict[str, Any]] = {}
        for row in columns:
            key = (row["table_schema"], row["table_name"])
            table = table_map.setdefault(key, {"schema": key[0], "table": key[1], "kind": row["table_type"], "primary_key": pk_map.get(key, []), "columns": []})
            table["columns"].append({"name": row["column_name"], "data_type": row["data_type"], "udt_name": row["udt_name"], "nullable": row["is_nullable"] == "YES", "default": row["column_default"], "position": row["ordinal_position"]})
        return {"database": database, "tables": list(table_map.values()), "foreign_keys": [json_safe(dict(row)) for row in foreign_keys]}

    def _catalog_sync(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        snapshot = self._catalog_snapshot(target)
        stored = self.catalog.replace_snapshot(target.id, snapshot)
        return ExecutionResult(ok=True, result={**stored, "schemas": sorted({item["schema"] for item in snapshot["tables"]})})

    def _catalog_resources(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        resources = self.catalog.list_resources(target.id, limit=int(arguments.get("limit") or 200))
        return ExecutionResult(ok=True, result={"target_id": target.id, "resources": resources, "count": len(resources)})

    def _catalog_bind(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        workspace_id = str(arguments.get("__workspace_id") or "")
        target_id = str(arguments.get("target_id") or "")
        alias = str(arguments.get("alias") or "")
        if not workspace_id or not target_id or not alias:
            raise ValueError("workspace context, target_id and alias are required")
        binding = self.catalog.bind_target(workspace_id, target_id, alias, is_default=bool(arguments.get("is_default", False)))
        return ExecutionResult(ok=True, result={"binding": binding, "executed_via_target": target.id})

    def _resource_policy(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        policy = arguments.get("policy") or {}
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        updated = self.catalog.update_resource_policy(target.id, str(arguments.get("resource") or ""), policy)
        return ExecutionResult(ok=True, result={"resource": updated})

    def _semantic_register(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        definition = arguments.get("definition") or {}
        if not isinstance(definition, dict):
            raise ValueError("definition must be an object")
        command = self.catalog.upsert_semantic_command(target.id, str(arguments.get("command") or ""), str(arguments.get("resource") or ""), str(arguments.get("action") or "search"), definition)
        return ExecutionResult(ok=True, result={"semantic_command": command})

    def _semantic_list(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        rows = self.catalog.list_semantic_commands(target.id)
        return ExecutionResult(ok=True, result={"target_id": target.id, "semantic_commands": rows, "count": len(rows)})

    def _resource(self, target: Target, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        name = str(arguments.get("resource") or "").strip()
        if not name:
            raise ValueError("resource is required")
        return self.catalog.get_resource(target.id, name)

    @staticmethod
    def _selected_columns(resource: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
        readable = list(resource.get("readable_columns") or [])
        requested = arguments.get("columns") or readable
        if not isinstance(requested, list) or not requested:
            raise ValueError("columns must be a non-empty array")
        selected = [str(item) for item in requested]
        if not set(selected).issubset(set(readable)):
            raise PermissionError("requested columns are outside the resource read policy")
        return selected

    @staticmethod
    def _where(filters: dict[str, Any], readable: set[str], sql_module) -> tuple[Any, list[Any]]:
        clauses = []
        params: list[Any] = []
        for raw_key, value in filters.items():
            key = str(raw_key)
            parts = key.rsplit("__", 1)
            column, operator = (parts[0], parts[1]) if len(parts) == 2 and parts[1] in _FILTER_SUFFIX else (key, "eq")
            if column not in readable:
                raise PermissionError(f"filter column is outside the resource read policy: {column}")
            identifier = sql_module.Identifier(column)
            if operator == "eq":
                clauses.append(sql_module.SQL("{} = %s").format(identifier)); params.append(value)
            elif operator == "contains":
                clauses.append(sql_module.SQL("{}::text ILIKE %s").format(identifier)); params.append(f"%{value}%")
            elif operator in {"gte", "lte", "gt", "lt"}:
                symbol = {"gte": ">=", "lte": "<=", "gt": ">", "lt": "<"}[operator]
                clauses.append(sql_module.SQL("{} " + symbol + " %s").format(identifier)); params.append(value)
            elif operator == "in":
                if not isinstance(value, list) or not value:
                    raise ValueError(f"{key} requires a non-empty array")
                clauses.append(sql_module.SQL("{} = ANY(%s)").format(identifier)); params.append(value)
            elif operator == "isnull":
                clauses.append(sql_module.SQL("{} IS {}NULL").format(identifier, sql_module.SQL("" if bool(value) else "NOT ")))
        return (sql_module.SQL(" AND ").join(clauses) if clauses else sql_module.SQL("TRUE")), params

    @staticmethod
    def _order(resource: dict[str, Any], arguments: dict[str, Any], selected: set[str], sql_module):
        raw = arguments.get("order_by") or resource.get("default_order") or []
        if isinstance(raw, str):
            raw = [raw]
        terms = []
        for item in raw:
            value = str(item)
            descending = value.startswith("-")
            column = value[1:] if descending else value
            if column not in selected:
                raise PermissionError("order column is outside the selected resource columns")
            terms.append(sql_module.SQL("{} {}").format(sql_module.Identifier(column), sql_module.SQL("DESC" if descending else "ASC")))
        return sql_module.SQL(", ").join(terms) if terms else None

    def _resource_search(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        resource = self._resource(target, arguments)
        selected = self._selected_columns(resource, arguments)
        filters = arguments.get("filters") or {}
        if not isinstance(filters, dict):
            raise ValueError("filters must be an object")
        _psycopg, sql_module, _dict_row = self._driver()
        where, params = self._where(filters, set(resource["readable_columns"]), sql_module)
        policy_limit = int((resource.get("policy") or {}).get("max_limit") or 1000)
        limit = self._limit(target, arguments, policy_limit=policy_limit)
        order = self._order(resource, arguments, set(selected), sql_module)
        query = sql_module.SQL("SELECT {} FROM {}.{} WHERE {}").format(sql_module.SQL(", ").join(map(sql_module.Identifier, selected)), sql_module.Identifier(resource["schema_name"]), sql_module.Identifier(resource["table_name"]), where)
        if order is not None:
            query += sql_module.SQL(" ORDER BY {} ").format(order)
        query += sql_module.SQL(" LIMIT %s")
        params.append(limit + 1)
        with self._connect(target) as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            rows = connection.execute(query, params).fetchall()
            connection.rollback()
        return ExecutionResult(ok=True, result={"resource": resource["resource_name"], "columns": selected, "rows": [json_safe(dict(row)) for row in rows[:limit]], "count": min(len(rows), limit), "truncated": len(rows) > limit, "target_id": target.id})

    def _key_filters(self, resource: dict[str, Any], key: Any) -> dict[str, Any]:
        primary = list(resource.get("primary_key") or [])
        if not primary:
            raise ValueError("resource has no primary key")
        if len(primary) == 1 and not isinstance(key, dict):
            return {primary[0]: key}
        if not isinstance(key, dict) or set(key) != set(primary):
            raise ValueError("composite resource key must provide every primary-key column")
        return key

    def _resource_show(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        resource = self._resource(target, arguments)
        key = arguments.get("key")
        if key is None:
            raise ValueError("key is required")
        return self._resource_search(target, {**arguments, "filters": self._key_filters(resource, key), "limit": 1})

    def _resource_update(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if target.config.get("read_only") is True:
            raise PermissionError("data target is read-only")
        resource = self._resource(target, arguments)
        writable = set(resource.get("writable_columns") or [])
        changes = arguments.get("changes") or {}
        if not isinstance(changes, dict) or not changes:
            raise ValueError("changes must be a non-empty object")
        if not set(changes).issubset(writable):
            raise PermissionError("changes contain columns outside the resource write policy")
        key_filters = self._key_filters(resource, arguments.get("key"))
        _psycopg, sql_module, _dict_row = self._driver()
        assignments = sql_module.SQL(", ").join(sql_module.SQL("{}=%s").format(sql_module.Identifier(column)) for column in changes)
        where, key_params = self._where(key_filters, set(resource["primary_key"]), sql_module)
        returning = list(dict.fromkeys([*resource["primary_key"], *changes.keys()]))
        query = sql_module.SQL("UPDATE {}.{} SET {} WHERE {} RETURNING {}").format(sql_module.Identifier(resource["schema_name"]), sql_module.Identifier(resource["table_name"]), assignments, where, sql_module.SQL(", ").join(map(sql_module.Identifier, returning)))
        with self._connect(target) as connection:
            row = connection.execute(query, [*changes.values(), *key_params]).fetchone()
            if not row:
                connection.rollback()
                raise KeyError("resource record not found")
            connection.commit()
        return ExecutionResult(ok=True, result={"resource": resource["resource_name"], "updated": json_safe(dict(row)), "target_id": target.id})

    def _semantic_query(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        if self.catalog is None:
            raise RuntimeError("data catalog is not configured")
        command_name = str(arguments.get("command") or "").strip().lower()
        params = arguments.get("params") or {}
        if not command_name:
            raise ValueError("command is required")
        if not isinstance(params, dict):
            raise ValueError("params must be an object")
        command = self.catalog.get_semantic_command(target.id, command_name)
        definition = command.get("definition") or {}
        filters = dict(definition.get("fixed_filters") or {})
        for param_name, mapping in (definition.get("param_filters") or {}).items():
            if isinstance(mapping, str):
                column_key, required, default = mapping, False, None
            else:
                column_key = str(mapping.get("column") or param_name)
                required = bool(mapping.get("required"))
                default = mapping.get("default")
            if param_name in params:
                filters[column_key] = params[param_name]
            elif default is not None:
                filters[column_key] = default
            elif required:
                raise ValueError(f"semantic command parameter is required: {param_name}")
        payload = {"resource": command["resource_name"], "filters": filters, "columns": definition.get("columns"), "order_by": definition.get("order_by"), "limit": definition.get("limit") or arguments.get("limit")}
        if command["action"] == "show":
            payload["key"] = params.get("key")
            return self._resource_show(target, payload)
        return self._resource_search(target, payload)
