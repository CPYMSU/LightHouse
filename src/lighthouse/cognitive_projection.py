from __future__ import annotations

import hashlib
import json
from typing import Any


ATLAS_MARKER = "Capability atlas: "


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _domain(name: str) -> str:
    for prefix, category in (
        ("agent.bus.", "agent-collaboration"),
        ("project.", "mega-project"),
        ("tools.", "tool-discovery"),
        ("data.", "data"),
        ("desktop.", "desktop"),
        ("system.git.", "repository"),
        ("system.test.", "testing"),
        ("system.file.", "repository"),
        ("system.project.", "repository"),
        ("system.service.", "service"),
        ("system.journal.", "service"),
        ("system.", "system"),
    ):
        if name.startswith(prefix):
            return category
    return name.split(".", 1)[0] or "general"


def _argument_manifest(value: Any) -> list[str]:
    result = []
    for name, raw in _dict(value).items():
        spec = _dict(raw)
        result.append(
            f"{name}:{spec.get('type') or 'any'}"
            + ("!" if spec.get("required") else "")
        )
    return result


def build_complete_capability_map(registry: Any, *, kernel: Any = None) -> dict[str, Any]:
    """Represent every callable tool without repeating full schema prose."""
    groups: dict[str, list[dict[str, Any]]] = {}
    names: list[str] = []
    for capability in registry.list(kernel=kernel):
        item = capability.public_dict() if hasattr(capability, "public_dict") else dict(capability)
        name = str(item.get("tool_name") or "")
        if not name:
            continue
        names.append(name)
        groups.setdefault(_domain(name), []).append(
            {
                "tool": name,
                "command": item.get("command") or name,
                "arguments": _argument_manifest(item.get("arguments")),
                "kernel": getattr(item.get("kernel"), "value", item.get("kernel")),
                "risk": getattr(item.get("risk"), "value", item.get("risk")),
                "confirmation": getattr(
                    item.get("confirmation"), "value", item.get("confirmation")
                ),
                "writes": bool(item.get("writes")),
            }
        )
    installed = set(names)
    return {
        "complete": True,
        "ranked": False,
        "semantic_limit": None,
        "tool_count": len(names),
        "exact_tool_names": names,
        "domains": [
            {"domain": name, "count": len(tools), "tools": tools}
            for name, tools in sorted(groups.items())
        ],
        "schema_expansion": {
            "available": "tools.inspect.v1" in installed,
            "capability": "tools.inspect.v1" if "tools.inspect.v1" in installed else None,
            "main_ai_may_expand_any_node": True,
            "main_ai_may_request_full_atlas": True,
        },
        "contract": (
            "Complete active-mode topology. Compaction removes repetition; it does not "
            "hide, rank, disable or replace any capability."
        ),
    }


def _step(step: Any) -> dict[str, Any] | None:
    if not isinstance(step, dict):
        return None
    payload = _dict(step.get("payload"))
    kind = str(step.get("kind") or "")
    result: dict[str, Any] = {
        "sequence": int(step.get("sequence") or 0),
        "kind": kind,
    }
    if kind == "decision":
        result.update(
            decision=payload.get("kind"),
            capability=payload.get("capability"),
            reason=payload.get("reason"),
        )
    elif kind == "observation":
        receipt = _dict(payload.get("receipt"))
        result.update(
            capability=payload.get("capability"),
            status=payload.get("status"),
            receipt_ok=receipt.get("ok"),
            result_hash=receipt.get("result_hash"),
        )
    elif kind in {"user_input", "user_direction", "input_required"}:
        result["message"] = payload.get("message")
    elif kind == "run_created":
        result["task"] = payload.get("task")
    elif kind in {"run_completed", "run_failed", "run_warning"}:
        result["message"] = payload.get("message") or payload.get("reason")
    else:
        for key in ("capability", "status", "reason", "error", "operation_id"):
            if key in payload:
                result[key] = payload.get(key)
    return {key: value for key, value in result.items() if value not in (None, "")}


def _ledger(steps: Any) -> dict[str, Any]:
    events = [item for item in (_step(raw) for raw in _list(steps)) if item]
    counts: dict[str, int] = {}
    for event in events:
        kind = str(event.get("kind") or "unknown")
        counts[kind] = counts.get(kind, 0) + 1
    return {
        "complete_event_index": True,
        "event_count": len(events),
        "counts": counts,
        "events": events,
        "raw_payloads_available": True,
    }


def _dialogue(state: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    steps = [item for item in _list(state.get("steps")) if isinstance(item, dict)]
    user = None
    for item in reversed(steps):
        if item.get("kind") in {"user_input", "user_direction"}:
            message = str(_dict(item.get("payload")).get("message") or "").strip()
            if message:
                user = {
                    "sequence": int(item.get("sequence") or 0),
                    "kind": item.get("kind"),
                    "content": message,
                }
                break
    prior = None
    ceiling = int(_dict(user).get("sequence") or 10**18)
    for item in reversed(steps):
        if int(item.get("sequence") or 0) >= ceiling or item.get("kind") != "input_required":
            continue
        message = str(_dict(item.get("payload")).get("message") or "").strip()
        if message:
            prior = {
                "sequence": int(item.get("sequence") or 0),
                "kind": "input_required",
                "content": message,
            }
            break
    run = _dict(state.get("run"))
    current = str(
        _dict(user).get("content")
        or _dict(context.get("current_request")).get("content")
        or run.get("task")
        or ""
    )
    return {
        "current_user_message": current,
        "original_run_request": run.get("task"),
        "latest_user_move": user,
        "preceding_assistant_move": prior,
        "recent_complete_turns": context.get("recent_turns") or [],
        "active_task": context.get("active_task"),
        "candidate_entities": context.get("candidate_entities") or [],
        "continuity_contract": (
            "Resolve the latest move against the preceding assistant move, complete recent "
            "turns, active task and subject before asking. It may answer, accept, reject, "
            "correct or challenge the previous move. This is semantic guidance, not routing."
        ),
    }


def _records(value: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {key: item.get(key) for key in keys if key in item}
        for item in _list(value)
        if isinstance(item, dict)
    ]


def _data_worlds(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    worlds = []
    for binding in _list(value.get("bindings")):
        if not isinstance(binding, dict):
            continue
        resources = []
        for resource in _list(binding.get("resources")):
            if not isinstance(resource, dict):
                continue
            columns = list(resource.get("readable_columns") or [])
            resources.append(
                {
                    "name": resource.get("name") or resource.get("resource_name"),
                    "primary_key": resource.get("primary_key") or [],
                    "readable_column_count": len(columns),
                    "readable_column_manifest": hashlib.sha256(
                        _json(columns).encode("utf-8")
                    ).hexdigest(),
                    "writable_columns": resource.get("writable_columns") or [],
                    "schema_expandable": True,
                }
            )
        commands = _records(
            binding.get("semantic_commands"),
            ("command_name", "command", "resource_name", "resource", "action"),
        )
        identity = {
            key: binding.get(key)
            for key in (
                "workspace_id",
                "target_id",
                "target_name",
                "alias",
                "is_default",
                "active",
            )
            if key in binding
        }
        worlds.append(
            {
                **identity,
                "resource_count": len(resources),
                "resources": resources,
                "semantic_command_count": len(commands),
                "semantic_commands": commands,
            }
        )
    result = {
        "available": value.get("available", True),
        "world_count": len(worlds),
        "worlds": worlds,
        "complete_within_catalog_snapshot": True,
        "detail_expansion": "Use data semantic, resource, schema or SQL capabilities.",
    }
    if value.get("error"):
        result.update(error=value.get("error"), error_type=value.get("error_type"))
    return result


def _project(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = {
        key: value.get(key)
        for key in (
            "project",
            "latest_checkpoint",
            "counts",
            "neuron_control",
            "fixed_workflow",
            "main_ai_may_investigate_plan_execute_or_revise_freely",
            "main_ai_may_wait_continue_parallelize_or_revise_freely",
            "active",
            "creation_is_optional",
            "main_ai_decides",
            "error",
        )
        if key in value
    }
    sections = {
        "critical_findings": ("id", "finding_type", "claim", "status", "confidence"),
        "current_steps": ("id", "title", "status", "kind", "owner", "dependencies"),
        "recent_decisions": ("id", "decision", "summary", "status", "reason"),
        "build_cells": ("id", "title", "goal", "status", "progress", "dependencies"),
        "contracts": ("id", "name", "title", "version", "contract_type", "status"),
        "active_write_leases": ("id", "path", "paths", "holder", "status", "expires_at"),
        "recent_batches": ("id", "title", "status", "paths", "changed_files"),
        "recent_integrations": ("id", "title", "status", "summary"),
        "worktrees": ("id", "path", "branch", "status"),
        "wiring": ("id", "feature", "title", "status", "path", "verified"),
    }
    for name, keys in sections.items():
        records = _records(value.get(name), keys)
        if records:
            result[name] = records
    result["raw_project_state_expandable"] = True
    return result


def _observatory(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    items = []
    for item in _list(value.get("items")):
        if not isinstance(item, dict):
            continue
        record = {
            key: item.get(key)
            for key in ("id", "role", "goal", "status", "progress", "criticality")
            if key in item
        }
        summary = _dict(item.get("result")).get("summary")
        if summary:
            record["result_summary"] = summary
        items.append(record)
    return {
        **{
            key: value.get(key)
            for key in ("total", "active", "queued", "completed")
            if key in value
        },
        "items": items,
    }


def compile_cognitive_projection(
    raw_state: dict[str, Any], *, capability_map: dict[str, Any]
) -> dict[str, Any]:
    """Fold duplicate views into one high-density, expandable world projection."""
    context = _dict(raw_state.get("context_intelligence")) or _dict(
        raw_state.get("memory")
    )
    neural = _dict(context.get("neuron_field")) or _dict(
        raw_state.get("neuron_field")
    )
    engineering = dict(_dict(raw_state.get("engineering")))
    engineering.pop("cognitive_continuity", None)
    focus = dict(_dict(context.get("tool_context")))
    neuron_focus = _dict(focus.get("neuron_control"))
    if neuron_focus:
        focus["neuron_control"] = {
            **neuron_focus,
            "candidate_limit_role": "initial_attention_resolution_only",
            "not_a_visibility_boundary": True,
        }
    activity = raw_state.get("agent_execution_activity")
    if isinstance(activity, dict):
        activity = activity.get("recent")
    projected = {
        "run": raw_state.get("run"),
        "workspace": raw_state.get("workspace"),
        "usage_context": raw_state.get("usage_context"),
        "dialogue_focus": _dialogue(raw_state, context),
        "cognitive_continuity": raw_state.get("cognitive_continuity"),
        "run_ledger": _ledger(raw_state.get("steps")),
        "memory_world": {
            "conversation_summary": context.get("conversation_summary"),
            "active_task": context.get("active_task"),
            "recent_tasks": context.get("recent_tasks") or [],
            "candidate_entities": context.get("candidate_entities") or [],
            "verified_facts": context.get("verified_facts") or [],
            "inferences": context.get("inferences") or [],
            "uncertainties": context.get("uncertainties") or [],
            "relevant_files": context.get("relevant_files") or [],
            "recent_locators": context.get("recent_locators") or [],
            "memory_index": context.get("memory_index"),
            "distillation": context.get("distillation"),
            "snapshot": context.get("snapshot"),
            "persistent_world_complete": True,
            "raw_memory_expandable": True,
        },
        "capability_world": {
            "complete_map": capability_map,
            "current_focus": focus,
            "main_ai_decides_expansion": True,
            "recommendations_advisory_only": True,
        },
        "data_worlds": _data_worlds(raw_state.get("data_worlds")),
        "project_world": {
            "active_project": context.get("active_project"),
            "director_brief": _project(context.get("project_director_brief")),
            "context_error": context.get("project_context_error"),
        },
        "agent_world": {
            "observatory": _observatory(
                context.get("agent_observatory")
                or raw_state.get("agent_observatory")
            ),
            "coordination_advice": context.get("coordination_advice")
            or raw_state.get("coordination_advice"),
            "results": _records(
                raw_state.get("agent_results"),
                (
                    "result_type",
                    "summary",
                    "findings",
                    "recommendations",
                    "risks",
                    "open_questions",
                    "changed_files",
                    "tests",
                    "remaining_risks",
                    "role",
                    "work_order_id",
                ),
            ),
            "execution_activity": activity,
        },
        "neuron_field": neural,
        "cognitive_control": raw_state.get("cognitive_control")
        or neural.get("cognitive_control"),
        "neuron_runtime_policy": raw_state.get("neuron_runtime_policy"),
        "work_intensity": raw_state.get("work_intensity"),
        "engineering": engineering,
    }
    projected = {
        key: value for key, value in projected.items() if value not in (None, {}, [])
    }
    raw_chars = len(_json(raw_state))
    compact_chars = len(_json(projected))
    projected["cognition_receipt"] = {
        "projection": "cognitive-state-v1",
        "fingerprint": hashlib.sha256(_json(projected).encode("utf-8")).hexdigest(),
        "raw_state_chars": raw_chars,
        "projected_state_chars": compact_chars,
        "duplicate_projections_folded": [
            "steps -> run_ledger + cognitive_continuity",
            "context_intelligence + memory -> memory_world",
            "tool atlas + recommendations -> capability_world",
            "agent observatory + execution + results -> agent_world",
        ],
        "world_coverage": {
            "conversation": "complete_recent_turns_plus_persistent_summary",
            "capabilities": "complete_compact_topology",
            "data": "catalog_topology_with_expandable_details",
            "tasks": "active_and_recent",
            "agents": "observatory_results_and_receipt_activity",
            "neuron_identity": "latest_persistent_snapshot",
        },
        "keyword_routing": False,
        "fixed_workflow_added": False,
        "semantic_restrictions_added": False,
        "main_ai_may_expand_any_world_node": True,
        "kernel_authority_unchanged": True,
    }
    return projected


class CognitiveProjectionMixin:
    """Project once after lower mixins have consumed the full runtime state."""

    def _model_state(self, run_id: str) -> dict[str, Any]:
        raw_state = super()._model_state(run_id)
        run = self.repository.get_agent_run(run_id)
        return compile_cognitive_projection(
            raw_state,
            capability_map=build_complete_capability_map(
                self.kernel.registry, kernel=run.mode
            ),
        )

    def _system_prompt(self, run) -> str:
        prompt = super()._system_prompt(run)
        prefix, marker, _atlas = prompt.rpartition(ATLAS_MARKER)
        replacement = (
            "The complete compact topology is supplied once in "
            "state.capability_world.complete_map. It lists every exact callable tool and "
            "compact argument contract for the active Kernel mode. It is not ranked, filtered "
            "or a semantic boundary. Expand any node through tools.inspect.v1 or another Tool "
            "Knowledge Registry capability when fuller schema detail is useful."
        )
        if marker:
            return prefix + ATLAS_MARKER + replacement
        return prompt + " " + ATLAS_MARKER + replacement
