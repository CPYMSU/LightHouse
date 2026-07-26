from __future__ import annotations

from typing import Any

from ..models import Capability, ExecutionResult, Target
from .agent_bus import AgentBusExecutor


class ElasticAgentBusExecutor(AgentBusExecutor):
    """Elastic Agent Bus 2.0 Work Orders, graphs and shared evidence."""

    def execute(
        self,
        capability: Capability,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        operation = capability.operation
        if operation == "dispatch_many":
            return self._dispatch_many(target, arguments)
        if operation == "results":
            ids = self._ids(arguments)
            items = [self.agent_bus.get_work_order(item) for item in ids]
            terminal = {"succeeded", "failed", "cancelled", "superseded"}
            return ExecutionResult(
                ok=True,
                result={
                    "items": items,
                    "count": len(items),
                    "terminal": sum(1 for item in items if item["status"] in terminal),
                    "pending": sum(1 for item in items if item["status"] not in terminal),
                    "waited": False,
                    "main_ai_decides_next_action": True,
                },
            )
        if operation == "wait_many":
            critical_roles = arguments.get("critical_roles")
            if critical_roles is not None and not isinstance(critical_roles, list):
                raise ValueError("critical_roles must be an array")
            value = self.agent_bus.wait_many(
                self._ids(arguments),
                timeout=float(arguments.get("wait_seconds") or 0),
                critical_roles=[str(item) for item in (critical_roles or [])],
            )
            return ExecutionResult(ok=True, result=value)
        if operation == "events":
            work_order_id = str(arguments.get("work_order_id") or "").strip()
            if not work_order_id:
                raise ValueError("work_order_id is required")
            items = self.agent_bus.work_events(
                work_order_id,
                after_id=int(arguments.get("after_id") or 0),
            )
            return ExecutionResult(
                ok=True,
                result={"items": items, "count": len(items), "work_order_id": work_order_id},
            )
        if operation == "findings":
            workspace_id = str(arguments.get("__workspace_id") or "")
            run_id = str(arguments.get("run_id") or arguments.get("parent_run_id") or "") or None
            items = self.agent_bus.shared_findings(
                workspace_id=workspace_id,
                parent_run_id=run_id,
                limit=int(arguments.get("limit") or 40),
            )
            return ExecutionResult(ok=True, result={"items": items, "count": len(items)})
        if operation == "conflicts":
            workspace_id = str(arguments.get("__workspace_id") or "")
            run_id = str(arguments.get("run_id") or arguments.get("parent_run_id") or "") or None
            items = self.agent_bus.active_conflicts(
                workspace_id=workspace_id,
                parent_run_id=run_id,
                limit=int(arguments.get("limit") or 50),
            )
            return ExecutionResult(ok=True, result={"items": items, "count": len(items)})
        if operation == "coordination":
            workspace_id = str(arguments.get("__workspace_id") or "")
            run_id = str(arguments.get("parent_run_id") or "") or None
            value = self.agent_bus.coordination_advice(
                workspace_id=workspace_id,
                parent_run_id=run_id,
                project_id=str(arguments.get("project_id") or "") or None,
            )
            if hasattr(self.agent_bus, "active_conflicts"):
                value["conflicts"] = self.agent_bus.active_conflicts(
                    workspace_id=workspace_id,
                    parent_run_id=run_id,
                    limit=30,
                )
            return ExecutionResult(ok=True, result={"coordination_advice": value})
        return super().execute(capability, target, arguments)

    def _dispatch_many(
        self,
        target: Target,
        arguments: dict[str, Any],
    ) -> ExecutionResult:
        values = arguments.get("work_orders")
        if not isinstance(values, list) or not values:
            raise ValueError("work_orders must be a non-empty array")
        workspace_id = str(arguments.get("__workspace_id") or "")
        parent_run_id = str(arguments.get("parent_run_id") or "") or None
        project_id = str(arguments.get("project_id") or "") or None
        actor = str(arguments.get("actor") or arguments.get("__actor") or "main-ai")
        shared = (
            dict(arguments.get("shared_payload") or {})
            if isinstance(arguments.get("shared_payload"), dict)
            else {}
        )
        items: list[dict[str, Any]] = []
        raw_dependencies: list[Any] = []
        for index, raw in enumerate(values):
            if not isinstance(raw, dict):
                raise ValueError(f"work_orders[{index}] must be an object")
            role = str(raw.get("role") or "").strip()
            goal = str(raw.get("goal") or "").strip()
            if not role or not goal:
                raise ValueError(f"work_orders[{index}] requires role and goal")
            payload = {**shared}
            if isinstance(raw.get("payload"), dict):
                payload.update(raw["payload"])
            assignment = (
                dict(payload.get("assignment") or {})
                if isinstance(payload.get("assignment"), dict)
                else {}
            )
            assignment.setdefault("goal", goal)
            assignment.setdefault("intent", str(raw.get("intent") or "investigate"))
            for key in ("scope", "deliverables", "preserve", "constraints"):
                if key in raw and key not in assignment:
                    assignment[key] = raw[key]
            payload["assignment"] = assignment
            if project_id:
                payload.setdefault("project_id", project_id)
            payload.setdefault("batch_index", index)
            payload.setdefault("main_ai_may_wait_or_continue", True)
            work = self.agent_bus.dispatch(
                workspace_id=workspace_id,
                parent_run_id=parent_run_id,
                requested_by=actor,
                role=role,
                goal=goal,
                payload=payload,
                priority=int(raw.get("priority") or 50),
                visibility=str(raw.get("visibility") or "foreground"),
            )
            items.append(work)
            raw_dependencies.append(raw.get("depends_on") or [])

        for index, raw in enumerate(values):
            dependencies = raw_dependencies[index]
            if not isinstance(dependencies, list) or not dependencies:
                continue
            resolved: list[str] = []
            for value in dependencies:
                if isinstance(value, int):
                    if value < 0 or value >= len(items):
                        raise ValueError(f"work_orders[{index}].depends_on index is out of range")
                    resolved.append(str(items[value]["id"]))
                    continue
                text = str(value or "").strip()
                if text.startswith("batch:") and text[6:].isdigit():
                    dependency_index = int(text[6:])
                    if dependency_index < 0 or dependency_index >= len(items):
                        raise ValueError(f"work_orders[{index}].depends_on batch index is out of range")
                    resolved.append(str(items[dependency_index]["id"]))
                elif text:
                    resolved.append(text)
            if resolved and hasattr(self.agent_bus, "add_dependencies"):
                items[index] = self.agent_bus.add_dependencies(items[index]["id"], resolved)

        unique_ids = {str(item.get("id")) for item in items}
        deduplicated = sum(1 for item in items if item.get("deduplicated"))
        return ExecutionResult(
            ok=True,
            result={
                "items": items,
                "accepted": len(unique_ids),
                "requested": len(items),
                "deduplicated": deduplicated,
                "queued": sum(1 for item in items if item.get("status") == "queued"),
                "collaboration_graph": True,
                "logical_agent_population_has_no_product_limit": True,
                "physical_concurrency": "adaptive durable queue",
                "main_ai_controls_expansion": True,
                "main_ai_controls_waiting": True,
                "parallel_build_then_review_supported": True,
            },
        )

    @staticmethod
    def _ids(arguments: dict[str, Any]) -> list[str]:
        ids = arguments.get("work_order_ids")
        if not isinstance(ids, list) or not ids:
            raise ValueError("work_order_ids must be a non-empty array")
        return [str(item) for item in ids if str(item)]
