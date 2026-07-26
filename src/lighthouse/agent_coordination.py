from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .work_intensity import resolve_intensity


_IMPLEMENTATION_ROLES = {"frontend", "backend", "data", "integration", "release"}
_VERIFICATION_ROLES = {"security", "test-design", "wiring-verification", "reality"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _clean_strings(values: Any, *, limit: int = 40) -> list[str]:
    if not isinstance(values, (list, tuple, set)):
        return []
    result: list[str] = []
    for item in list(values)[:limit]:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def execution_profile_for_role(role: str) -> str:
    role = str(role or "").strip().lower()
    if role in _IMPLEMENTATION_ROLES:
        return "integration" if role == "integration" else "release" if role == "release" else "implementation"
    if role in _VERIFICATION_ROLES:
        return "verification"
    return "advisory"


def prepare_work_order_payload(role: str, goal: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    value = dict(payload or {})
    assignment = dict(value.get("assignment") or {}) if isinstance(value.get("assignment"), dict) else {}
    scope = dict(assignment.get("scope") or {}) if isinstance(assignment.get("scope"), dict) else {}
    scope["paths"] = _clean_strings(scope.get("paths"))
    scope["symbols"] = _clean_strings(scope.get("symbols"))
    deliverables = _clean_strings(assignment.get("deliverables"))
    preserve = _clean_strings(assignment.get("preserve"))
    intent = str(assignment.get("intent") or value.get("intent") or "investigate").strip().lower()
    profile = str(assignment.get("execution_profile") or execution_profile_for_role(role))
    assignment.update(
        {
            "goal": str(assignment.get("goal") or goal),
            "intent": intent,
            "parent_goal": str(assignment.get("parent_goal") or value.get("parent_goal") or ""),
            "scope": scope,
            "deliverables": deliverables,
            "preserve": preserve,
            "execution_profile": profile,
            "constraints": {
                "main_ai_is_project_director": True,
                "existing_code_first": True,
                "no_unreceipted_execution_claims": True,
                **(
                    dict(assignment.get("constraints") or {})
                    if isinstance(assignment.get("constraints"), dict)
                    else {}
                ),
            },
        }
    )
    intensity = value.get("intensity")
    if isinstance(intensity, dict):
        selected = intensity.get("selected") or intensity.get("name") or "balanced"
    else:
        selected = intensity or "balanced"
    policy = resolve_intensity(selected)
    coordination = dict(value.get("coordination") or {}) if isinstance(value.get("coordination"), dict) else {}
    dedupe_material = {
        "role": str(role or "").strip().lower(),
        "goal": " ".join(str(goal or "").lower().split()),
        "intent": intent,
        "paths": scope["paths"],
        "symbols": scope["symbols"],
        "deliverables": deliverables,
    }
    coordination.setdefault("dedupe_key", _fingerprint(dedupe_material))
    coordination.setdefault("goal_fingerprint", _fingerprint(dedupe_material["goal"]))
    coordination.setdefault(
        "scope_fingerprint",
        _fingerprint({"paths": scope["paths"], "symbols": scope["symbols"]}),
    )
    coordination.setdefault("collaboration_depth", int(value.get("collaboration_depth") or 0))
    write_paths = scope["paths"] if profile in {"implementation", "integration", "release"} else []
    coordination.setdefault(
        "write_intent",
        {
            "paths": write_paths,
            "mode": "modify" if write_paths else "none",
            "status": "proposed" if write_paths else "not_required",
        },
    )
    value["assignment"] = assignment
    value["intensity"] = {"selected": policy.name, "effective": policy.public_dict()}
    value["coordination"] = coordination
    value.setdefault(
        "local_cognitive_state",
        {
            "understanding": "",
            "strategy": "",
            "verified_facts": [],
            "open_questions": [],
            "active_files": scope["paths"],
            "next_intent": "inspect the assigned scope and gather execution evidence",
        },
    )
    value.setdefault("shared_cognitive_brief", {})
    value.setdefault("shared_findings", [])
    return value


def merge_work_payload(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing or {})
    for key, value in incoming.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = merge_work_payload(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            merged: list[Any] = []
            seen: set[str] = set()
            for item in [*current, *value]:
                fingerprint = _canonical(item)
                if fingerprint in seen:
                    continue
                seen.add(fingerprint)
                merged.append(item)
            result[key] = merged[-80:]
        elif value not in {None, ""}:
            result[key] = value
    return result


def build_shared_cognitive_brief(
    *,
    cognitive_state: dict[str, Any] | None,
    intensity: dict[str, Any] | None,
    findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    state = dict(cognitive_state or {})
    return {
        "goal": state.get("goal") or {},
        "user_directions": list(state.get("user_directions") or [])[-12:],
        "current_understanding": state.get("understanding") or {},
        "current_strategy": state.get("strategy") or {},
        "verified_facts": list(state.get("verified_facts") or [])[-30:],
        "open_questions": list(state.get("open_questions") or [])[-20:],
        "active_work": state.get("active_work") or {},
        "work_items": list(state.get("work_items") or [])[-20:],
        "current_diff": {
            "changed_files": list((state.get("active_work") or {}).get("changed_files") or [])[-30:]
        },
        "related_findings": list(findings or [])[-30:],
        "intensity": dict(intensity or state.get("work_intensity") or {}),
        "instruction": (
            "Use this brief as shared evidence. User directions outrank earlier strategy; "
            "verified facts outrank assumptions; do not repeat completed investigation without new evidence."
        ),
    }


def patch_paths(arguments: dict[str, Any]) -> list[str]:
    patch = str(arguments.get("patch") or "")
    values: list[str] = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if path and path != "/dev/null" and path not in values:
                values.append(path)
    return values


def write_paths_for_tool(capability: str, arguments: dict[str, Any]) -> list[str]:
    capability = str(capability or "")
    if capability == "system.file.patch.v1":
        return patch_paths(arguments)
    if capability.startswith("system.file.write"):
        return _clean_strings([arguments.get("path")])
    if capability.startswith("project."):
        return _clean_strings([arguments.get("path"), arguments.get("cwd")])
    return []


def normalise_collaboration_requests(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        values = [value]
    elif isinstance(value, list):
        values = value
    else:
        return []
    result: list[dict[str, Any]] = []
    for item in values[:8]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        goal = str(item.get("goal") or "").strip()
        if not role or not goal:
            continue
        result.append(
            {
                "role": role,
                "goal": goal,
                "reason": str(item.get("reason") or "specialist collaboration requested"),
                "scope": dict(item.get("scope") or {}) if isinstance(item.get("scope"), dict) else {},
                "deliverables": _clean_strings(item.get("deliverables")),
                "priority": max(0, min(int(item.get("priority") or 50), 100)),
            }
        )
    return result


def tool_call_signature(call: dict[str, Any]) -> str:
    return _fingerprint(
        {
            "capability": call.get("capability"),
            "arguments": call.get("arguments") if isinstance(call.get("arguments"), dict) else {},
        }
    )


def semantic_terms(value: str) -> set[str]:
    normalized = re.sub(r"[^a-z0-9\u3400-\u9fff]+", " ", str(value or "").lower())
    return {item for item in normalized.split() if len(item) > 1}
