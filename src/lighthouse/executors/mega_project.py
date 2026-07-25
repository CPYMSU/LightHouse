from __future__ import annotations

from typing import Any

from ..models import Capability, ExecutionResult, Target


class MegaProjectExecutor:
    """Expose composable project and tool-knowledge primitives to the main AI."""

    def __init__(self, *, tool_registry, project_store):
        self.tool_registry = tool_registry
        self.project_store = project_store

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        operation = capability.operation
        workspace_id = str(arguments.get("__workspace_id") or "")
        actor = str(arguments.get("actor") or "main-ai")

        if operation == "tools_search":
            items = self.tool_registry.search(
                str(arguments.get("query") or ""),
                categories=tuple(arguments.get("categories") or ()),
                limit=int(arguments.get("limit") or 20),
            )
            return ExecutionResult(ok=True, result={"items": items, "count": len(items)})
        if operation == "tools_inspect":
            value = self.tool_registry.inspect(str(arguments.get("tool_name") or ""))
            return ExecutionResult(ok=True, result={"tool": value})
        if operation == "tools_recommend":
            value = self.tool_registry.recommend(
                str(arguments.get("query") or ""),
                workspace_id=workspace_id,
                run_id=str(arguments.get("director_run_id") or "") or None,
                project_id=str(arguments.get("project_id") or "") or None,
                limit=int(arguments.get("limit") or 12),
            )
            return ExecutionResult(ok=True, result=value)
        if operation == "project_create":
            project = self.project_store.create_project(
                workspace_id=workspace_id,
                title=str(arguments.get("title") or ""),
                goal=str(arguments.get("goal") or ""),
                conversation_id=str(arguments.get("conversation_id") or "") or None,
                director_run_id=str(arguments.get("director_run_id") or "") or None,
                phase=str(arguments.get("phase") or "adaptive"),
                metadata=arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {},
            )
            return ExecutionResult(ok=True, result={"project": project})
        if operation == "project_inspect":
            value = self.project_store.inspect_project(str(arguments.get("project_id") or ""))
            return ExecutionResult(ok=True, result=value)
        if operation == "project_checkpoint":
            checkpoint = self.project_store.checkpoint(
                project_id=str(arguments.get("project_id") or ""),
                summary=str(arguments.get("summary") or ""),
                phase=str(arguments.get("phase") or "adaptive"),
                payload=arguments.get("payload") if isinstance(arguments.get("payload"), dict) else {},
                created_by=actor,
            )
            return ExecutionResult(ok=True, result={"checkpoint": checkpoint})
        if operation == "finding_store":
            finding = self.project_store.store_finding(
                project_id=str(arguments.get("project_id") or ""),
                finding_type=str(arguments.get("finding_type") or "inference"),
                claim=str(arguments.get("claim") or ""),
                domain=str(arguments.get("domain") or "general"),
                confidence=float(arguments.get("confidence") or 0.5),
                evidence=arguments.get("evidence") if isinstance(arguments.get("evidence"), list) else [],
                source_work_order_id=str(arguments.get("source_work_order_id") or "") or None,
                supersedes_id=str(arguments.get("supersedes_id") or "") or None,
                metadata=arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {},
            )
            return ExecutionResult(ok=True, result={"finding": finding})
        if operation == "finding_search":
            items = self.project_store.search_findings(
                project_id=str(arguments.get("project_id") or ""),
                query=str(arguments.get("query") or ""),
                finding_types=tuple(arguments.get("finding_types") or ()),
                limit=int(arguments.get("limit") or 40),
            )
            return ExecutionResult(ok=True, result={"items": items, "count": len(items)})
        if operation == "step_create":
            step = self.project_store.create_step(
                project_id=str(arguments.get("project_id") or ""),
                title=str(arguments.get("title") or ""),
                goal=str(arguments.get("goal") or ""),
                phase=str(arguments.get("phase") or "adaptive"),
                status=str(arguments.get("status") or "proposed"),
                sequence=int(arguments.get("sequence") or 0),
                parent_step_id=str(arguments.get("parent_step_id") or "") or None,
                dependencies=arguments.get("dependencies") if isinstance(arguments.get("dependencies"), list) else [],
                metadata=arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {},
            )
            return ExecutionResult(ok=True, result={"step": step})
        if operation == "step_update":
            step = self.project_store.update_step(
                str(arguments.get("step_id") or ""),
                status=str(arguments["status"]) if arguments.get("status") is not None else None,
                assigned_work_order_id=(
                    str(arguments.get("assigned_work_order_id") or "")
                    if "assigned_work_order_id" in arguments
                    else None
                ),
                implementation_receipts=(
                    arguments.get("implementation_receipts")
                    if isinstance(arguments.get("implementation_receipts"), list)
                    else None
                ),
                verification=(
                    arguments.get("verification")
                    if isinstance(arguments.get("verification"), dict)
                    else None
                ),
                metadata=arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else None,
            )
            return ExecutionResult(ok=True, result={"step": step})
        raise ValueError(f"unsupported Mega Project operation: {operation}")
