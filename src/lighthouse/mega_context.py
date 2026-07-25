from __future__ import annotations

from typing import Any

from .neuron_context import NeuronAwareContextCompiler


class MegaProjectContextCompiler(NeuronAwareContextCompiler):
    """Add advisory tool discovery and durable project knowledge to AI context."""

    def __init__(
        self,
        memory,
        agent_bus,
        neuron_runtime,
        tool_registry,
        project_store,
    ):
        super().__init__(memory, agent_bus, neuron_runtime)
        self.tool_registry = tool_registry
        self.project_store = project_store

    def compile(self, **kwargs: Any) -> dict[str, Any]:
        bundle = super().compile(**kwargs)
        workspace_id = str(kwargs.get("workspace_id") or "")
        conversation_id = str(kwargs.get("conversation_id") or "") or None
        run_id = str(kwargs.get("run_id") or "") or None
        query = str(kwargs.get("query") or "")

        try:
            project = self.project_store.active_project(
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                run_id=run_id,
            )
        except Exception as exc:
            project = None
            bundle["project_context_error"] = str(exc)

        project_id = str(project.get("id") or "") if project else None
        try:
            bundle["tool_context"] = self.tool_registry.recommend(
                query,
                workspace_id=workspace_id,
                run_id=run_id,
                project_id=project_id,
                limit=16,
            )
            bundle["tool_context"]["categories"] = self.tool_registry.categories()
        except Exception as exc:
            bundle["tool_context"] = {
                "recommendations": [],
                "tool_search_available": True,
                "advisory_only": True,
                "error": str(exc),
            }

        bundle["active_project"] = project
        if project:
            try:
                detail = self.project_store.inspect_project(project["id"])
                findings = detail.get("findings") or []
                steps = detail.get("steps") or []
                decisions = detail.get("decisions") or []
                bundle["project_director_brief"] = {
                    "project": project,
                    "critical_findings": findings[:24],
                    "current_steps": steps[:40],
                    "recent_decisions": decisions[:12],
                    "latest_checkpoint": detail.get("latest_checkpoint"),
                    "counts": {
                        "findings": len(findings),
                        "steps": len(steps),
                        "decisions": len(decisions),
                    },
                    "fixed_workflow": False,
                    "main_ai_may_investigate_plan_execute_or_revise_freely": True,
                }
            except Exception as exc:
                bundle["project_director_brief"] = {
                    "project": project,
                    "error": str(exc),
                    "fixed_workflow": False,
                }
        else:
            bundle["project_director_brief"] = {
                "active": False,
                "creation_is_optional": True,
                "main_ai_decides": True,
            }

        bundle["snapshot"] = {
            **(bundle.get("snapshot") or {}),
            "tool_registry_source": "postgres",
            "mega_project_source": "postgres",
        }
        return bundle
