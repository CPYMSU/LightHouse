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
        neural = (
            bundle.get("neuron_field")
            if isinstance(bundle.get("neuron_field"), dict)
            else {}
        )
        control = (
            neural.get("cognitive_control")
            if isinstance(neural.get("cognitive_control"), dict)
            else {}
        )
        tool_limit = max(6, min(32, int(control.get("candidate_count") or 20)))
        planning_depth = max(
            0.0, min(float(control.get("planning_depth", 0.5)), 1.0)
        )
        project_scale = 0.6 + 0.8 * planning_depth

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
                limit=tool_limit,
            )
            bundle["tool_context"]["categories"] = self.tool_registry.categories()
            bundle["tool_context"]["neuron_control"] = {
                "candidate_limit": tool_limit,
                "source": "persistent_24_neuron_field",
                "prompt_persona": False,
            }
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
                "queued": sum(
                    1 for item in work_orders if item.get("status") == "queued"
                ),
                "completed": sum(
                    1 for item in work_orders if item.get("status") in terminal
                ),
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
                finding_limit = max(12, min(40, round(24 * project_scale)))
                step_limit = max(20, min(64, round(40 * project_scale)))
                decision_limit = max(8, min(24, round(12 * project_scale)))
                bundle["project_director_brief"] = {
                    "project": project,
                    "critical_findings": findings[:finding_limit],
                    "current_steps": steps[:step_limit],
                    "recent_decisions": decisions[:decision_limit],
                    "latest_checkpoint": detail.get("latest_checkpoint"),
                    "build_cells": (massive.get("cells") or [])[
                        : max(20, step_limit)
                    ],
                    "contracts": (massive.get("contracts") or [])[
                        : max(24, step_limit)
                    ],
                    "active_write_leases": (massive.get("active_write_leases") or [])[
                        :step_limit
                    ],
                    "recent_batches": (massive.get("batches") or [])[:step_limit],
                    "recent_integrations": (massive.get("integrations") or [])[
                        :finding_limit
                    ],
                    "worktrees": (massive.get("worktrees") or [])[:step_limit],
                    "wiring": (massive.get("wiring") or [])[
                        : max(20, finding_limit)
                    ],
                    "counts": {
                        "findings": len(findings),
                        "steps": len(steps),
                        "decisions": len(decisions),
                        "cells": len(massive.get("cells") or []),
                        "contracts": len(massive.get("contracts") or []),
                        "batches": len(massive.get("batches") or []),
                        "integrations": len(massive.get("integrations") or []),
                    },
                    "neuron_control": {
                        "planning_depth": planning_depth,
                        "project_scale": project_scale,
                        "prompt_persona": False,
                    },
                    "fixed_workflow": False,
                    "main_ai_may_investigate_plan_execute_or_revise_freely": True,
                    "main_ai_may_wait_continue_parallelize_or_revise_freely": True,
                    "massive_output_emerges_from_verified_batches": True,
                }
            except Exception as exc:
                bundle["project_director_brief"] = {
                    "project": project,
                    "error": str(exc),
                    "fixed_workflow": False,
                    "main_ai_may_investigate_plan_execute_or_revise_freely": True,
                }
        else:
            bundle["project_director_brief"] = {
                "active": False,
                "creation_is_optional": True,
                "main_ai_decides": True,
                "main_ai_may_investigate_plan_execute_or_revise_freely": True,
                "massive_build_tools_available": self.massive_build is not None,
            }

        bundle["snapshot"] = {
            **(bundle.get("snapshot") or {}),
            "tool_registry_source": "postgres",
            "mega_project_source": "postgres",
            "massive_build_source": (
                "postgres" if self.massive_build is not None else "unavailable"
            ),
            "agent_coordination_source": "postgres",
            "neuron_tool_budget_applied": True,
        }
        return bundle
