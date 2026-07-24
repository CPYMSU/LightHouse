from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from .models import KernelMode, Workspace, json_safe

_ALIAS = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_COMMAND = re.compile(r"^[a-z][a-z0-9_.-]{1,127}$")


def _node_id(target_id: str, kind: str, qualified_name: str) -> str:
    return hashlib.sha256(f"{target_id}:{kind}:{qualified_name}".encode()).hexdigest()


class DataCatalog(Protocol):
    def bind_target(self, workspace_id: str, target_id: str, alias: str, *, is_default: bool = False) -> dict[str, Any]: ...
    def list_bindings(self, workspace_id: str) -> list[dict[str, Any]]: ...
    def resolve_target(self, workspace_id: str, alias: str | None, fallback: str | None) -> str: ...
    def replace_snapshot(self, target_id: str, snapshot: dict[str, Any]) -> dict[str, Any]: ...
    def list_resources(self, target_id: str, *, limit: int = 200) -> list[dict[str, Any]]: ...
    def get_resource(self, target_id: str, resource_name: str) -> dict[str, Any]: ...
    def update_resource_policy(self, target_id: str, resource_name: str, policy: dict[str, Any]) -> dict[str, Any]: ...
    def upsert_semantic_command(self, target_id: str, command_name: str, resource_name: str, action: str, definition: dict[str, Any]) -> dict[str, Any]: ...
    def list_semantic_commands(self, target_id: str) -> list[dict[str, Any]]: ...
    def get_semantic_command(self, target_id: str, command_name: str) -> dict[str, Any]: ...
    def context(self, workspace_id: str, *, resource_limit: int = 80) -> dict[str, Any]: ...


class InMemoryDataCatalog:
    def __init__(self) -> None:
        self.bindings: dict[tuple[str, str], dict[str, Any]] = {}
        self.resources: dict[tuple[str, str], dict[str, Any]] = {}
        self.commands: dict[tuple[str, str], dict[str, Any]] = {}
        self.snapshots: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def bind_target(self, workspace_id: str, target_id: str, alias: str, *, is_default: bool = False) -> dict[str, Any]:
        alias = alias.strip().lower()
        if not _ALIAS.fullmatch(alias):
            raise ValueError("data target alias is invalid")
        with self._lock:
            if is_default:
                for key, item in list(self.bindings.items()):
                    if item["workspace_id"] == workspace_id:
                        self.bindings[key] = {**item, "is_default": False}
            item = {"workspace_id": workspace_id, "target_id": target_id, "alias": alias, "is_default": bool(is_default), "active": True}
            self.bindings[(workspace_id, alias)] = item
            return dict(item)

    def list_bindings(self, workspace_id: str) -> list[dict[str, Any]]:
        return [dict(item) for item in self.bindings.values() if item["workspace_id"] == workspace_id and item["active"]]

    def resolve_target(self, workspace_id: str, alias: str | None, fallback: str | None) -> str:
        if alias:
            item = self.bindings.get((workspace_id, alias.strip().lower()))
            if not item or not item["active"]:
                raise KeyError(f"workspace data target alias not found: {alias}")
            return str(item["target_id"])
        defaults = [item for item in self.list_bindings(workspace_id) if item["is_default"]]
        if defaults:
            return str(defaults[0]["target_id"])
        if fallback:
            return fallback
        bindings = self.list_bindings(workspace_id)
        if len(bindings) == 1:
            return str(bindings[0]["target_id"])
        raise ValueError("workspace has no default data target")

    def replace_snapshot(self, target_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.snapshots[target_id] = json_safe(snapshot)
            seen: set[str] = set()
            for table in snapshot.get("tables") or []:
                resource_name = f"{table['schema']}.{table['table']}"
                seen.add(resource_name)
                current = self.resources.get((target_id, resource_name), {})
                columns = [item["name"] for item in table.get("columns") or []]
                resource = {
                    "target_id": target_id,
                    "resource_name": resource_name,
                    "schema_name": table["schema"],
                    "table_name": table["table"],
                    "primary_key": list(table.get("primary_key") or []),
                    "readable_columns": columns,
                    "writable_columns": list(current.get("writable_columns") or []),
                    "display_column": current.get("display_column"),
                    "default_order": list(current.get("default_order") or []),
                    "policy": dict(current.get("policy") or {}),
                    "active": True,
                }
                self.resources[(target_id, resource_name)] = resource
            for key, item in list(self.resources.items()):
                if key[0] == target_id and key[1] not in seen:
                    self.resources[key] = {**item, "active": False}
        return {"target_id": target_id, "tables": len(snapshot.get("tables") or []), "foreign_keys": len(snapshot.get("foreign_keys") or [])}

    def list_resources(self, target_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        rows = [dict(item) for (tid, _), item in self.resources.items() if tid == target_id and item.get("active", True)]
        return sorted(rows, key=lambda item: item["resource_name"])[: max(1, min(limit, 1000))]

    def get_resource(self, target_id: str, resource_name: str) -> dict[str, Any]:
        exact = self.resources.get((target_id, resource_name))
        if exact and exact.get("active", True):
            return dict(exact)
        matches = [item for item in self.list_resources(target_id) if item["table_name"] == resource_name]
        if len(matches) == 1:
            return matches[0]
        raise KeyError(f"data resource not found: {resource_name}")

    def update_resource_policy(self, target_id: str, resource_name: str, policy: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.get_resource(target_id, resource_name)
            readable = set(current["readable_columns"])
            writable = [str(item) for item in policy.get("writable_columns") or []]
            if not set(writable).issubset(readable):
                raise ValueError("writable columns must be readable resource columns")
            display = policy.get("display_column")
            if display and display not in readable:
                raise ValueError("display_column must be a readable resource column")
            current.update({"writable_columns": writable, "display_column": display, "default_order": list(policy.get("default_order") or []), "policy": dict(policy.get("policy") or {})})
            self.resources[(target_id, current["resource_name"])] = current
            return dict(current)

    def upsert_semantic_command(self, target_id: str, command_name: str, resource_name: str, action: str, definition: dict[str, Any]) -> dict[str, Any]:
        command_name = command_name.strip().lower()
        if not _COMMAND.fullmatch(command_name):
            raise ValueError("semantic command name is invalid")
        self.get_resource(target_id, resource_name)
        if action not in {"search", "show"}:
            raise ValueError("semantic command action must be search or show")
        item = {"target_id": target_id, "command_name": command_name, "resource_name": resource_name, "action": action, "definition": json_safe(definition), "active": True}
        self.commands[(target_id, command_name)] = item
        return dict(item)

    def list_semantic_commands(self, target_id: str) -> list[dict[str, Any]]:
        return sorted([dict(item) for (tid, _), item in self.commands.items() if tid == target_id and item.get("active", True)], key=lambda item: item["command_name"])

    def get_semantic_command(self, target_id: str, command_name: str) -> dict[str, Any]:
        item = self.commands.get((target_id, command_name.strip().lower()))
        if not item or not item.get("active", True):
            raise KeyError(f"semantic data command not found: {command_name}")
        return dict(item)

    def context(self, workspace_id: str, *, resource_limit: int = 80) -> dict[str, Any]:
        bindings = self.list_bindings(workspace_id)
        worlds = []
        for binding in bindings:
            resources = self.list_resources(str(binding["target_id"]), limit=resource_limit)
            worlds.append({**binding, "resources": [{"name": item["resource_name"], "primary_key": item["primary_key"], "readable_columns": item["readable_columns"][:30], "writable_columns": item["writable_columns"]} for item in resources], "semantic_commands": self.list_semantic_commands(str(binding["target_id"]))[:50]})
        return {"bindings": worlds, "count": len(worlds)}


class PostgresDataCatalog:
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("core PostgreSQL DSN is required")
        import psycopg
        from psycopg.rows import dict_row
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.dsn = dsn

    def _connect(self):
        return self._psycopg.connect(self.dsn, row_factory=self._dict_row)

    @staticmethod
    def _row(row: dict[str, Any]) -> dict[str, Any]:
        return {key: (str(value) if value is not None and (key.endswith("_id") or type(value).__name__ == "UUID") else value.isoformat() if hasattr(value, "isoformat") else value) for key, value in row.items()}

    def bind_target(self, workspace_id: str, target_id: str, alias: str, *, is_default: bool = False) -> dict[str, Any]:
        alias = alias.strip().lower()
        if not _ALIAS.fullmatch(alias):
            raise ValueError("data target alias is invalid")
        with self._connect() as connection:
            target = connection.execute("SELECT kind FROM lh_targets WHERE id=%s AND active=TRUE", (target_id,)).fetchone()
            if not target or target["kind"] != "data":
                raise ValueError("target_id does not reference an active data target")
            if is_default:
                connection.execute("UPDATE lh_workspace_data_targets SET is_default=FALSE WHERE workspace_id=%s", (workspace_id,))
                connection.execute("UPDATE lh_workspaces SET data_target_id=%s,updated_at=now() WHERE id=%s", (target_id, workspace_id))
            row = connection.execute(
                """INSERT INTO lh_workspace_data_targets(workspace_id,target_id,alias,is_default,active)
                   VALUES (%s,%s,%s,%s,TRUE)
                   ON CONFLICT (workspace_id,alias) DO UPDATE
                   SET target_id=EXCLUDED.target_id,is_default=EXCLUDED.is_default,active=TRUE,updated_at=now()
                   RETURNING workspace_id,target_id,alias,is_default,active,created_at,updated_at""",
                (workspace_id, target_id, alias, bool(is_default)),
            ).fetchone()
        return self._row(row)

    def list_bindings(self, workspace_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("""SELECT b.workspace_id,b.target_id,b.alias,b.is_default,b.active,b.created_at,b.updated_at,t.name AS target_name
                                         FROM lh_workspace_data_targets b JOIN lh_targets t ON t.id=b.target_id
                                         WHERE b.workspace_id=%s AND b.active=TRUE ORDER BY b.is_default DESC,b.alias""", (workspace_id,)).fetchall()
            if not rows:
                rows = connection.execute("""SELECT w.id AS workspace_id,w.data_target_id AS target_id,'default' AS alias,TRUE AS is_default,TRUE AS active,
                                                     w.created_at,w.updated_at,t.name AS target_name
                                              FROM lh_workspaces w JOIN lh_targets t ON t.id=w.data_target_id
                                              WHERE w.id=%s AND w.data_target_id IS NOT NULL""", (workspace_id,)).fetchall()
        return [self._row(row) for row in rows]

    def resolve_target(self, workspace_id: str, alias: str | None, fallback: str | None) -> str:
        with self._connect() as connection:
            if alias:
                row = connection.execute("SELECT target_id FROM lh_workspace_data_targets WHERE workspace_id=%s AND alias=%s AND active=TRUE", (workspace_id, alias.strip().lower())).fetchone()
                if not row:
                    raise KeyError(f"workspace data target alias not found: {alias}")
                return str(row["target_id"])
            row = connection.execute("SELECT target_id FROM lh_workspace_data_targets WHERE workspace_id=%s AND active=TRUE ORDER BY is_default DESC,alias LIMIT 1", (workspace_id,)).fetchone()
        if row:
            return str(row["target_id"])
        if fallback:
            return fallback
        raise ValueError("workspace has no data target")

    def replace_snapshot(self, target_id: str, snapshot: dict[str, Any]) -> dict[str, Any]:
        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        database = str(snapshot.get("database") or "database")
        db_id = _node_id(target_id, "database", database)
        nodes.append({"id": db_id, "kind": "database", "qualified_name": database, "parent_id": None, "title": database, "payload": {}})
        for table in snapshot.get("tables") or []:
            schema = str(table["schema"])
            table_name = str(table["table"])
            schema_id = _node_id(target_id, "schema", schema)
            if not any(item["id"] == schema_id for item in nodes):
                nodes.append({"id": schema_id, "kind": "schema", "qualified_name": schema, "parent_id": db_id, "title": schema, "payload": {}})
                edges.append({"from": db_id, "to": schema_id, "relation": "contains", "metadata": {}})
            qualified = f"{schema}.{table_name}"
            table_id = _node_id(target_id, "table", qualified)
            nodes.append({"id": table_id, "kind": "table", "qualified_name": qualified, "parent_id": schema_id, "title": table_name, "payload": {"table_kind": table.get("kind"), "primary_key": table.get("primary_key") or []}})
            edges.append({"from": schema_id, "to": table_id, "relation": "contains", "metadata": {}})
            for column in table.get("columns") or []:
                column_qualified = f"{qualified}.{column['name']}"
                column_id = _node_id(target_id, "column", column_qualified)
                nodes.append({"id": column_id, "kind": "column", "qualified_name": column_qualified, "parent_id": table_id, "title": column["name"], "payload": column})
                edges.append({"from": table_id, "to": column_id, "relation": "contains", "metadata": {}})
        for foreign_key in snapshot.get("foreign_keys") or []:
            source = _node_id(target_id, "table", f"{foreign_key['source_schema']}.{foreign_key['source_table']}")
            destination = _node_id(target_id, "table", f"{foreign_key['target_schema']}.{foreign_key['target_table']}")
            edges.append({"from": source, "to": destination, "relation": "references", "metadata": foreign_key})

        with self._connect() as connection:
            connection.execute("DELETE FROM lh_schema_edges WHERE target_id=%s", (target_id,))
            connection.execute("DELETE FROM lh_schema_nodes WHERE target_id=%s", (target_id,))
            for node in nodes:
                payload = node["payload"]
                connection.execute("""INSERT INTO lh_schema_nodes(id,target_id,kind,qualified_name,parent_id,title,payload,content_hash)
                                      VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""", (node["id"], target_id, node["kind"], node["qualified_name"], node["parent_id"], node["title"], json.dumps(payload), hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()))
            for edge in edges:
                connection.execute("""INSERT INTO lh_schema_edges(target_id,from_node_id,to_node_id,relation,metadata)
                                      VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""", (target_id, edge["from"], edge["to"], edge["relation"], json.dumps(edge["metadata"])))
            seen_resources: list[str] = []
            for table in snapshot.get("tables") or []:
                resource_name = f"{table['schema']}.{table['table']}"
                seen_resources.append(resource_name)
                columns = [item["name"] for item in table.get("columns") or []]
                connection.execute("""INSERT INTO lh_data_resources(id,target_id,resource_name,schema_name,table_name,primary_key,readable_columns)
                                      VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                                      ON CONFLICT (target_id,resource_name) DO UPDATE
                                      SET schema_name=EXCLUDED.schema_name,table_name=EXCLUDED.table_name,
                                          primary_key=EXCLUDED.primary_key,readable_columns=EXCLUDED.readable_columns,
                                          active=TRUE,updated_at=now()""", (str(uuid4()), target_id, resource_name, table["schema"], table["table"], json.dumps(table.get("primary_key") or []), json.dumps(columns)))
            if seen_resources:
                connection.execute("UPDATE lh_data_resources SET active=FALSE,updated_at=now() WHERE target_id=%s AND NOT (resource_name=ANY(%s))", (target_id, seen_resources))
            else:
                connection.execute("UPDATE lh_data_resources SET active=FALSE,updated_at=now() WHERE target_id=%s", (target_id,))
        return {"target_id": target_id, "database": database, "nodes": len(nodes), "edges": len(edges), "resources": len(seen_resources)}

    def list_resources(self, target_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM lh_data_resources WHERE target_id=%s AND active=TRUE ORDER BY resource_name LIMIT %s", (target_id, max(1, min(limit, 1000)))).fetchall()
        return [self._row(row) for row in rows]

    def get_resource(self, target_id: str, resource_name: str) -> dict[str, Any]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM lh_data_resources WHERE target_id=%s AND active=TRUE AND (resource_name=%s OR table_name=%s) ORDER BY CASE WHEN resource_name=%s THEN 0 ELSE 1 END", (target_id, resource_name, resource_name, resource_name)).fetchall()
        if not rows:
            raise KeyError(f"data resource not found: {resource_name}")
        exact = [row for row in rows if row["resource_name"] == resource_name]
        if exact:
            return self._row(exact[0])
        if len(rows) == 1:
            return self._row(rows[0])
        raise ValueError(f"resource name is ambiguous; use schema.table: {resource_name}")

    def update_resource_policy(self, target_id: str, resource_name: str, policy: dict[str, Any]) -> dict[str, Any]:
        current = self.get_resource(target_id, resource_name)
        readable = set(current["readable_columns"])
        writable = [str(item) for item in policy.get("writable_columns") or []]
        if not set(writable).issubset(readable):
            raise ValueError("writable columns must be readable resource columns")
        display = policy.get("display_column")
        if display and display not in readable:
            raise ValueError("display_column must be a readable resource column")
        with self._connect() as connection:
            row = connection.execute("""UPDATE lh_data_resources SET writable_columns=%s::jsonb,display_column=%s,
                                      default_order=%s::jsonb,policy=%s::jsonb,updated_at=now()
                                      WHERE target_id=%s AND resource_name=%s RETURNING *""", (json.dumps(writable), display, json.dumps(policy.get("default_order") or []), json.dumps(policy.get("policy") or {}), target_id, current["resource_name"])).fetchone()
        return self._row(row)

    def upsert_semantic_command(self, target_id: str, command_name: str, resource_name: str, action: str, definition: dict[str, Any]) -> dict[str, Any]:
        command_name = command_name.strip().lower()
        if not _COMMAND.fullmatch(command_name):
            raise ValueError("semantic command name is invalid")
        resource = self.get_resource(target_id, resource_name)
        if action not in {"search", "show"}:
            raise ValueError("semantic command action must be search or show")
        with self._connect() as connection:
            row = connection.execute("""INSERT INTO lh_semantic_commands(id,target_id,command_name,resource_name,action,definition)
                                      VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                                      ON CONFLICT (target_id,command_name) DO UPDATE
                                      SET resource_name=EXCLUDED.resource_name,action=EXCLUDED.action,definition=EXCLUDED.definition,active=TRUE,updated_at=now()
                                      RETURNING *""", (str(uuid4()), target_id, command_name, resource["resource_name"], action, json.dumps(definition))).fetchone()
        return self._row(row)

    def list_semantic_commands(self, target_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM lh_semantic_commands WHERE target_id=%s AND active=TRUE ORDER BY command_name", (target_id,)).fetchall()
        return [self._row(row) for row in rows]

    def get_semantic_command(self, target_id: str, command_name: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM lh_semantic_commands WHERE target_id=%s AND command_name=%s AND active=TRUE", (target_id, command_name.strip().lower())).fetchone()
        if not row:
            raise KeyError(f"semantic data command not found: {command_name}")
        return self._row(row)

    def context(self, workspace_id: str, *, resource_limit: int = 80) -> dict[str, Any]:
        bindings = self.list_bindings(workspace_id)
        worlds = []
        for binding in bindings:
            target_id = str(binding["target_id"])
            resources = self.list_resources(target_id, limit=resource_limit)
            worlds.append({**binding, "resources": [{"name": item["resource_name"], "primary_key": item["primary_key"], "readable_columns": item["readable_columns"][:30], "writable_columns": item["writable_columns"]} for item in resources], "semantic_commands": self.list_semantic_commands(target_id)[:50]})
        return {"bindings": worlds, "count": len(worlds)}


@dataclass
class DataTargetResolver:
    catalog: DataCatalog

    def resolve(self, workspace: Workspace, kernel: KernelMode, arguments: dict[str, Any]) -> str | None:
        if kernel == KernelMode.DATA:
            alias = arguments.get("target_alias") or arguments.get("target")
            return self.catalog.resolve_target(workspace.id, str(alias) if alias else None, workspace.data_target_id)
        if kernel == KernelMode.SYSTEM:
            return workspace.system_target_id
        if kernel == KernelMode.DESKTOP:
            return workspace.desktop_target_id
        return None
