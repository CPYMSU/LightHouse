from __future__ import annotations

from typing import Any

from .neuron_context import NeuronAwareContextCompiler


class MegaProjectContextCompiler(NeuronAwareContextCompiler):
    """Compile tool discovery, project knowledge, Agent advice and Massive Build state."""

    def __init__(
        self,
        memory,
        agent_bus,
        neuron_runtime,
        tool_registry,
        project_store,
        massive_build=None,
    ):
        super().__init__(memory, agent_bus, neuron_runtime)
        self.tool_registry = tool_registry
        self.project_store = project_store
        self.massive_build = massive_build

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
                limit=20,
            )
            bundle["tool_context"]["categories"] = self.tool_registry.categories()
        except Exception as exc:
            bundle["tool_context"] = {
                "recommendations": [],
                "tool_search_available": True,
                "advisory_only": True,
                "error": str(exc),
            }

        try:
            observatory = self.agent_bus.observatory(
                workspace_id=workspace_id,
                parent_run_id=run_id,
            )
        except Exception:
            work_orders = bundle.get("work_orders") or []
            terminal = {"succeeded", "failed", "cancelled", "superseded"}
            active = {"leased", "running", "waiting_dependency", "waiting_confirmation"}
            observatory = {
                "total": len(work_orders),
                "active": sum(1 for item in work_orders if item.get("status") in active),
                "queued": sum(1 for item in work_orders if item.get("status") == "queued"),
                "completed": sum(1 for item in work_orders if item.get("status") in terminal),
                "items": work_orders,
            }
        bundle["agent_observatory"] = observatory
        try:
            bundle["coordination_advice"] = self.agent_bus.coordination_advice(
                workspace_id=workspace_id,
                parent_run_id=run_id,
                project_id=project_id,
            )
        except Exception as exc:
            bundle["coordination_advice"] = {
                "recommended_strategy": "main_ai_decides",
                "advisory_only": True,
                "main_ai_may_wait_or_continue": True,
                "error": str(exc),
            }

        bundle["active_project"] = project
        if project:
            try:
                detail = self.project_store.inspect_project(project["id"])
                findings = detail.get("findings") or []
                steps = detail.get("steps") or []
                decisions = detail.get("decisions") or []
                massive = (
                    self.massive_build.project_brief(project["id"])
                    if self.massive_build is not None
                    else {}
                )
                bundle["project_director_brief"] = {
                    "project": project,
                    "critical_findings": findings[:24],
                    "current_steps": steps[:40],
                    "recent_decisions": decisions[:12],
                    "latest_checkpoint": detail.get("latest_checkpoint"),
                    "build_cells": (massive.get("cells") or [])[:50],
                    "contracts": (massive.get("contracts") or [])[:60],
                    "active_write_leases": (massive.get("active_write_leases") or [])[:40],
                    "recent_batches": (massive.get("batches") or [])[:40],
                    "recent_integrations": (massive.get("integrations") or [])[:20],
                    "worktrees": (massive.get("worktrees") or [])[:40],
                    "wiring": (massive.get("wiring") or [])[:50],
                    "counts": {
                        "findings": len(findings),
                        "steps": len(steps),
                        "decisions": len(decisions),
                        "cells": len(massive.get("cells") or []),
                        "contracts": len(massive.get("contracts") or []),
                        "batches": len(massive.get("batches") or []),
                        "integrations": len(massive.get("integrations") or []),
                    },
                    "fixed_workflow": False,
                    "main_ai_may_wait_continue_parallelize_or_revise_freely": True,
                    "massive_output_emerges_from_verified_batches": True,
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
                "massive_build_tools_available": self.massive_build is not None,
            }

        bundle["snapshot"] = {
            **(bundle.get("snapshot") or {}),
            "tool_registry_source": "postgres",
            "mega_project_source": "postgres",
            "massive_build_source": "postgres" if self.massive_build is not None else "unavailable",
            "agent_coordination_source": "postgres",
        }
        return bundle
