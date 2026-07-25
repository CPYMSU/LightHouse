from __future__ import annotations

from pathlib import Path
from typing import Any

from ..models import Capability, ExecutionResult, Target


class AgentBusExecutor:
    """Execute Agent Bus operations while keeping specialist work durable."""

    def __init__(self, *, agent_bus, context_compiler, repository, registry):
        self.agent_bus = agent_bus
        self.context_compiler = context_compiler
        self.repository = repository
        self.registry = registry

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        operation = capability.operation
        if operation == "dispatch":
            return self._dispatch(target, arguments)
        if operation == "status":
            work_order_id = str(arguments.get("work_order_id") or "").strip()
            if not work_order_id:
                raise ValueError("work_order_id is required")
            value = self.agent_bus.wait_for_work_order(
                work_order_id,
                timeout=float(arguments.get("wait_seconds") or 0),
            )
            return ExecutionResult(ok=True, result={"work_order": value})
        if operation == "cancel":
            work_order_id = str(arguments.get("work_order_id") or "").strip()
            if not work_order_id:
                raise ValueError("work_order_id is required")
            payload = (
                arguments.get("payload")
                if isinstance(arguments.get("payload"), dict)
                else {}
            )
            value = self.agent_bus.cancel(
                work_order_id,
                requested_by=str(
                    arguments.get("__actor")
                    or payload.get("actor")
                    or "main-ai"
                ),
            )
            return ExecutionResult(ok=True, result={"work_order": value})
        raise ValueError(f"unsupported Agent Bus operation: {operation}")

    def _dispatch(self, target: Target, arguments: dict[str, Any]) -> ExecutionResult:
        workspace_id = str(arguments.get("__workspace_id") or "")
        parent_run_id = str(arguments.get("parent_run_id") or "") or None
        role = str(arguments.get("role") or "").strip()
        goal = str(arguments.get("goal") or "").strip()
        payload = (
            arguments.get("payload")
            if isinstance(arguments.get("payload"), dict)
            else {}
        )
        actor = str(
            arguments.get("__actor")
            or payload.get("actor")
            or "main-ai"
        )
        visibility = str(arguments.get("visibility") or "foreground")
        work = self.agent_bus.dispatch(
            workspace_id=workspace_id,
            parent_run_id=parent_run_id,
            requested_by=actor,
            role=role,
            goal=goal,
            payload=payload,
            priority=int(arguments.get("priority") or 50),
            visibility=visibility,
        )
        if role == "context-investigator":
            bundle = self.context_compiler.compile(
                workspace_id=workspace_id,
                actor=actor,
                conversation_id=str(payload.get("conversation_id") or "") or None,
                run_id=parent_run_id,
                query=str(payload.get("query") or goal),
                force=bool(payload.get("force")),
            )
            work = self.agent_bus.complete(work["id"], result={"context": bundle})
        elif role == "file-reality":
            result = self._inspect_path(target, payload)
            work = self.agent_bus.complete(work["id"], result=result)
        elif role == "authorization":
            result = self._inspect_authorization(target, payload)
            work = self.agent_bus.complete(work["id"], result=result)
        elif role == "receipt-verification":
            result = self._inspect_receipt(payload)
            work = self.agent_bus.complete(work["id"], result=result)
        elif role == "memory-steward":
            job_type = str(payload.get("job_type") or "memory.conversation.distill")
            job = self.agent_bus.enqueue_background_job(
                workspace_id=workspace_id,
                conversation_id=str(payload.get("conversation_id") or "") or None,
                run_id=parent_run_id,
                work_order_id=work["id"],
                job_type=job_type,
                payload=payload,
                coalesce_key=str(payload.get("coalesce_key") or "") or None,
                priority=int(arguments.get("priority") or 20),
            )
            return ExecutionResult(
                ok=True,
                result={"work_order": work, "background_job": job},
            )
        return ExecutionResult(ok=True, result={"work_order": work})

    @staticmethod
    def _inspect_path(target: Target, payload: dict[str, Any]) -> dict[str, Any]:
        raw = str(payload.get("path") or "").strip()
        if not raw:
            raise ValueError("file-reality investigation requires path")
        default_cwd = Path(str(target.config.get("default_cwd") or "/")).expanduser().resolve()
        roots = [
            Path(str(item)).expanduser().resolve()
            for item in (target.config.get("allowed_roots") or [default_cwd])
        ]
        proposed = Path(raw).expanduser()
        candidate = proposed.resolve() if proposed.is_absolute() else (default_cwd / proposed).resolve()
        inside = any(candidate == root or exists,
                "inside_allowed_roots": inside,
                "is_file": candidate.is_file() if exists else False,
                "is_directory": candidate.is_dir() if exists else False,
                "is_symlink": candidate.is_symlink(),
                "size_bytes": candidate.stat().st_size if exists and candidate.is_file() else None,
                "modified_at": candidate.stat().st_mtime if exists else None,
            },
            "observed_by": "file-reality-agent",
        }

    def _inspect_authorization(self, target: Target, payload: dict[str, Any]) -> dict[str, Any]:
        capability_name = str(payload.get("capability") or "").strip()
        capability = self.registry.get(capability_name) if capability_name else None
        raw_path = str(payload.get("path") or "").strip()
        roots = [
            str(Path(str(item)).expanduser().resolve())
            for item in (
                target.config.get("allowed_roots")
                or [target.config.get("default_cwd") or "/"]
            )
        ]
        path_authorized = None
        canonical_path = None
        if raw_path:
            candidate = Path(raw_path).expanduser()
            if not candidate.is_absolute():
                candidate = Path(str(target.config.get("default_cwd") or "/")) / candidate
            candidate = candidate.resolve()
            canonical_path = str(candidate)
            path_authorized = any(
                candidate == Path(root) or Path(root) in candidate.parents for root in roots
            )
        return {
            "actor_authorization": {
                "target_id": target.id,
                "target_kind": target.kind.value,
                "allowed_roots": roots,
                "capability": capability.public_dict() if capability else None,
                "canonical_path": canonical_path,
                "path_authorized": path_authorized,
            },
            "observed_by": "authorization-agent",
        }

    def _inspect_receipt(self, payload: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(payload.get("operation_id") or "").strip()
        if not operation_id:
            raise ValueError("receipt-verification requires operation_id")
        operation = self.repository.get_operation(operation_id)
        receipt = self.repository.get_receipt(operation_id)
        execution_verified = bool(receipt and receipt.get("ok") is True)
        return {
            "operation": operation.public_dict(),
            "receipt": receipt,
            "execution_verified": execution_verified,
            "goal_verified": False,
            "missing_evidence": (
                []
                if not execution_verified
                else ["The Receipt proves execution, not the user's semantic goal."]
            ),
            "observed_by": "receipt-verification-agent",
        }
