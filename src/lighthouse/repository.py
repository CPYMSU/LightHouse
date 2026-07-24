from __future__ import annotations

from dataclasses import replace
import json
from threading import RLock
from typing import Any, Protocol
from uuid import uuid4

from .models import KernelMode, OperationStatus, OperationView, Target, TargetKind, Workspace, digest_json, utc_now


class Repository(Protocol):
    def create_target(self, *, name: str, kind: TargetKind, config: dict[str, Any]) -> Target: ...
    def list_targets(self) -> list[Target]: ...
    def get_target(self, target_id: str) -> Target: ...
    def create_workspace(self, *, name: str, data_target_id: str | None, system_target_id: str | None) -> Workspace: ...
    def list_workspaces(self) -> list[Workspace]: ...
    def get_workspace(self, workspace_id: str) -> Workspace: ...
    def create_operation(self, *, operation_id: str, workspace_id: str, target_id: str, capability: str, kernel: KernelMode, actor: str, envelope: dict[str, Any], idempotency_key: str | None) -> OperationView: ...
    def get_operation(self, operation_id: str) -> OperationView: ...
    def set_operation_status(self, operation_id: str, status: OperationStatus) -> OperationView: ...
    def claim_operation(self, operation_id: str, expected: OperationStatus) -> OperationView | None: ...
    def append_event(self, operation_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]: ...
    def list_events(self, operation_id: str) -> list[dict[str, Any]]: ...
    def save_receipt(self, operation_id: str, *, ok: bool, result: dict[str, Any]) -> dict[str, Any]: ...
    def get_receipt(self, operation_id: str) -> dict[str, Any] | None: ...


def _request_hash(envelope: dict[str, Any]) -> str:
    value = dict(envelope)
    value.pop("operation_id", None)
    return digest_json(value)


class InMemoryRepository:
    def __init__(self) -> None:
        self.targets: dict[str, Target] = {}
        self.workspaces: dict[str, Workspace] = {}
        self.operations: dict[str, OperationView] = {}
        self.events: dict[str, list[dict[str, Any]]] = {}
        self.receipts: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def create_target(self, *, name: str, kind: TargetKind, config: dict[str, Any]) -> Target:
        with self._lock:
            if any(item.name == name for item in self.targets.values()):
                raise ValueError("target name already exists")
            target = Target(id=str(uuid4()), name=name, kind=kind, config=dict(config))
            self.targets[target.id] = target
            return target

    def list_targets(self) -> list[Target]:
        return list(self.targets.values())

    def get_target(self, target_id: str) -> Target:
        try:
            return self.targets[target_id]
        except KeyError as exc:
            raise KeyError("target not found") from exc

    def create_workspace(self, *, name: str, data_target_id: str | None, system_target_id: str | None) -> Workspace:
        with self._lock:
            if any(item.name == name for item in self.workspaces.values()):
                raise ValueError("workspace name already exists")
            for target_id, kind in ((data_target_id, TargetKind.DATA), (system_target_id, TargetKind.SYSTEM)):
                if target_id is not None and self.get_target(target_id).kind != kind:
                    raise ValueError(f"workspace {kind.value} target has the wrong kind")
            workspace = Workspace(id=str(uuid4()), name=name, data_target_id=data_target_id, system_target_id=system_target_id)
            self.workspaces[workspace.id] = workspace
            return workspace

    def list_workspaces(self) -> list[Workspace]:
        return list(self.workspaces.values())

    def get_workspace(self, workspace_id: str) -> Workspace:
        try:
            return self.workspaces[workspace_id]
        except KeyError as exc:
            raise KeyError("workspace not found") from exc

    def create_operation(self, *, operation_id: str, workspace_id: str, target_id: str, capability: str, kernel: KernelMode, actor: str, envelope: dict[str, Any], idempotency_key: str | None) -> OperationView:
        with self._lock:
            request_hash = _request_hash(envelope)
            if idempotency_key:
                for item in self.operations.values():
                    if item.envelope.get("idempotency_key") == idempotency_key:
                        if item.request_hash != request_hash:
                            raise ValueError("idempotency key is already bound to another request")
                        return item
            now = utc_now()
            view = OperationView(id=operation_id, status=OperationStatus.CREATED, capability=capability, kernel=kernel, target_id=target_id, workspace_id=workspace_id, actor=actor, envelope=envelope, envelope_hash=digest_json(envelope), request_hash=request_hash, created_at=now, updated_at=now)
            self.operations[operation_id] = view
            self.events[operation_id] = []
            return view

    def get_operation(self, operation_id: str) -> OperationView:
        try:
            return self.operations[operation_id]
        except KeyError as exc:
            raise KeyError("operation not found") from exc

    def set_operation_status(self, operation_id: str, status: OperationStatus) -> OperationView:
        with self._lock:
            current = self.get_operation(operation_id)
            updated = replace(current, status=status, updated_at=utc_now())
            self.operations[operation_id] = updated
            return updated

    def claim_operation(self, operation_id: str, expected: OperationStatus) -> OperationView | None:
        with self._lock:
            current = self.get_operation(operation_id)
            if current.status != expected:
                return None
            return self.set_operation_status(operation_id, OperationStatus.RUNNING)

    def append_event(self, operation_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.get_operation(operation_id)
            sequence = len(self.events.setdefault(operation_id, [])) + 1
            event = {"sequence": sequence, "type": event_type, "payload": payload, "created_at": utc_now().isoformat()}
            self.events[operation_id].append(event)
            return event

    def list_events(self, operation_id: str) -> list[dict[str, Any]]:
        self.get_operation(operation_id)
        return list(self.events.get(operation_id, []))

    def save_receipt(self, operation_id: str, *, ok: bool, result: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self.get_operation(operation_id)
            receipt = {"operation_id": operation_id, "ok": bool(ok), "result": result, "result_hash": digest_json(result), "created_at": utc_now().isoformat()}
            existing = self.receipts.get(operation_id)
            if existing and existing["result_hash"] != receipt["result_hash"]:
                raise ValueError("operation receipt is immutable")
            self.receipts[operation_id] = existing or receipt
            return self.receipts[operation_id]

    def get_receipt(self, operation_id: str) -> dict[str, Any] | None:
        self.get_operation(operation_id)
        return self.receipts.get(operation_id)


class PostgresRepository:
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
    def _target(row: dict[str, Any]) -> Target:
        return Target(id=str(row["id"]), name=row["name"], kind=TargetKind(row["kind"]), config=row["config"], active=row["active"])

    @staticmethod
    def _workspace(row: dict[str, Any]) -> Workspace:
        return Workspace(id=str(row["id"]), name=row["name"], data_target_id=str(row["data_target_id"]) if row["data_target_id"] else None, system_target_id=str(row["system_target_id"]) if row["system_target_id"] else None)

    @staticmethod
    def _operation(row: dict[str, Any]) -> OperationView:
        return OperationView(id=str(row["id"]), status=OperationStatus(row["status"]), capability=row["capability"], kernel=KernelMode(row["kernel"]), target_id=str(row["target_id"]), workspace_id=str(row["workspace_id"]), actor=row["actor"], envelope=row["envelope"], envelope_hash=row["envelope_hash"], request_hash=row["request_hash"], created_at=row["created_at"], updated_at=row["updated_at"])

    def migrate(self, sql: str) -> None:
        with self._connect() as connection:
            connection.execute(sql)

    def create_target(self, *, name: str, kind: TargetKind, config: dict[str, Any]) -> Target:
        with self._connect() as connection:
            row = connection.execute("INSERT INTO lh_targets(id,name,kind,config) VALUES (%s,%s,%s,%s::jsonb) RETURNING *", (str(uuid4()), name, kind.value, json.dumps(config))).fetchone()
        return self._target(row)

    def list_targets(self) -> list[Target]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM lh_targets ORDER BY name").fetchall()
        return [self._target(row) for row in rows]

    def get_target(self, target_id: str) -> Target:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM lh_targets WHERE id=%s AND active=TRUE", (target_id,)).fetchone()
        if not row:
            raise KeyError("target not found")
        return self._target(row)

    def create_workspace(self, *, name: str, data_target_id: str | None, system_target_id: str | None) -> Workspace:
        with self._connect() as connection:
            row = connection.execute("INSERT INTO lh_workspaces(id,name,data_target_id,system_target_id) VALUES (%s,%s,%s,%s) RETURNING *", (str(uuid4()), name, data_target_id, system_target_id)).fetchone()
        return self._workspace(row)

    def list_workspaces(self) -> list[Workspace]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM lh_workspaces ORDER BY name").fetchall()
        return [self._workspace(row) for row in rows]

    def get_workspace(self, workspace_id: str) -> Workspace:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM lh_workspaces WHERE id=%s", (workspace_id,)).fetchone()
        if not row:
            raise KeyError("workspace not found")
        return self._workspace(row)

    def create_operation(self, *, operation_id: str, workspace_id: str, target_id: str, capability: str, kernel: KernelMode, actor: str, envelope: dict[str, Any], idempotency_key: str | None) -> OperationView:
        envelope_hash = digest_json(envelope)
        request_hash = _request_hash(envelope)
        with self._connect() as connection:
            row = connection.execute("""INSERT INTO lh_operations(id,workspace_id,target_id,capability,kernel,actor,status,envelope,envelope_hash,request_hash,idempotency_key)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
                ON CONFLICT (idempotency_key) DO UPDATE SET idempotency_key=EXCLUDED.idempotency_key
                RETURNING *""", (operation_id, workspace_id, target_id, capability, kernel.value, actor, OperationStatus.CREATED.value, json.dumps(envelope), envelope_hash, request_hash, idempotency_key)).fetchone()
            if row["request_hash"] != request_hash:
                raise ValueError("idempotency key is already bound to another request")
        return self._operation(row)

    def get_operation(self, operation_id: str) -> OperationView:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM lh_operations WHERE id=%s", (operation_id,)).fetchone()
        if not row:
            raise KeyError("operation not found")
        return self._operation(row)

    def set_operation_status(self, operation_id: str, status: OperationStatus) -> OperationView:
        with self._connect() as connection:
            row = connection.execute("UPDATE lh_operations SET status=%s,updated_at=now() WHERE id=%s RETURNING *", (status.value, operation_id)).fetchone()
        if not row:
            raise KeyError("operation not found")
        return self._operation(row)

    def claim_operation(self, operation_id: str, expected: OperationStatus) -> OperationView | None:
        with self._connect() as connection:
            row = connection.execute("UPDATE lh_operations SET status=%s,updated_at=now() WHERE id=%s AND status=%s RETURNING *", (OperationStatus.RUNNING.value, operation_id, expected.value)).fetchone()
        return self._operation(row) if row else None

    def append_event(self, operation_id: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as connection:
            locked = connection.execute("SELECT id FROM lh_operations WHERE id=%s FOR UPDATE", (operation_id,)).fetchone()
            if not locked:
                raise KeyError("operation not found")
            row = connection.execute("""INSERT INTO lh_operation_events(operation_id,sequence,event_type,payload)
                SELECT %s,COALESCE(MAX(sequence),0)+1,%s,%s::jsonb FROM lh_operation_events WHERE operation_id=%s
                RETURNING sequence,event_type,payload,created_at""", (operation_id, event_type, json.dumps(payload), operation_id)).fetchone()
        return {"sequence": row["sequence"], "type": row["event_type"], "payload": row["payload"], "created_at": row["created_at"].isoformat()}

    def list_events(self, operation_id: str) -> list[dict[str, Any]]:
        self.get_operation(operation_id)
        with self._connect() as connection:
            rows = connection.execute("SELECT sequence,event_type,payload,created_at FROM lh_operation_events WHERE operation_id=%s ORDER BY sequence", (operation_id,)).fetchall()
        return [{"sequence": row["sequence"], "type": row["event_type"], "payload": row["payload"], "created_at": row["created_at"].isoformat()} for row in rows]

    def save_receipt(self, operation_id: str, *, ok: bool, result: dict[str, Any]) -> dict[str, Any]:
        result_hash = digest_json(result)
        with self._connect() as connection:
            row = connection.execute("""INSERT INTO lh_operation_receipts(operation_id,ok,result,result_hash)
                VALUES (%s,%s,%s::jsonb,%s)
                ON CONFLICT (operation_id) DO UPDATE SET operation_id=EXCLUDED.operation_id
                RETURNING operation_id,ok,result,result_hash,created_at""", (operation_id, ok, json.dumps(result), result_hash)).fetchone()
            if row["result_hash"] != result_hash:
                raise ValueError("operation receipt is immutable")
        return {"operation_id": str(row["operation_id"]), "ok": row["ok"], "result": row["result"], "result_hash": row["result_hash"], "created_at": row["created_at"].isoformat()}

    def get_receipt(self, operation_id: str) -> dict[str, Any] | None:
        self.get_operation(operation_id)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM lh_operation_receipts WHERE operation_id=%s", (operation_id,)).fetchone()
        if not row:
            return None
        return {"operation_id": str(row["operation_id"]), "ok": row["ok"], "result": row["result"], "result_hash": row["result_hash"], "created_at": row["created_at"].isoformat()}
