"""Progressive, token-budgeted views over the durable Memory Fabric.

The database remains the complete source of memory. A model turn receives an
index-sized capsule first and explicitly asks to expand only when that capsule
cannot resolve the current task. This keeps durable recall explainable without
turning every prompt into a transcript dump.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


_DEPTHS = ("index", "focused", "deep")


@dataclass(frozen=True)
class MemoryResolutionPolicy:
    depth: str
    turn_limit: int
    task_limit: int
    entity_limit: int
    fact_limit: int
    inference_limit: int
    uncertainty_limit: int
    file_limit: int
    locator_limit: int
    summary_chars: int
    item_chars: int


_POLICIES = {
    "index": MemoryResolutionPolicy(
        depth="index",
        turn_limit=1,
        task_limit=3,
        entity_limit=6,
        fact_limit=8,
        inference_limit=4,
        uncertainty_limit=4,
        file_limit=5,
        locator_limit=5,
        summary_chars=1_200,
        item_chars=260,
    ),
    "focused": MemoryResolutionPolicy(
        depth="focused",
        turn_limit=4,
        task_limit=6,
        entity_limit=12,
        fact_limit=16,
        inference_limit=8,
        uncertainty_limit=8,
        file_limit=10,
        locator_limit=10,
        summary_chars=2_800,
        item_chars=520,
    ),
    "deep": MemoryResolutionPolicy(
        depth="deep",
        turn_limit=8,
        task_limit=12,
        entity_limit=24,
        fact_limit=32,
        inference_limit=16,
        uncertainty_limit=16,
        file_limit=20,
        locator_limit=20,
        summary_chars=5_600,
        item_chars=1_000,
    ),
}


def normalize_memory_depth(value: Any, *, default: str = "index") -> str:
    depth = str(value or default).strip().lower()
    if depth not in _POLICIES:
        raise ValueError("memory depth must be index, focused, or deep")
    return depth


def memory_resolution_policy(value: Any, *, default: str = "index") -> MemoryResolutionPolicy:
    return _POLICIES[normalize_memory_depth(value, default=default)]


def compact_memory_context(
    memory: dict[str, Any],
    *,
    depth: str = "index",
    candidates: list[dict[str, Any]] | None = None,
    facts: list[dict[str, Any]] | None = None,
    inferences: list[dict[str, Any]] | None = None,
    uncertainties: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return the bounded memory view delivered to one model turn.

    The inputs can retain full database retrieval results for ranking and cache
    construction. This function is the only boundary that decides what crosses
    into the model prompt.
    """

    policy = memory_resolution_policy(depth)
    source = memory if isinstance(memory, dict) else {}
    summary = _summary(source.get("conversation_summary"), policy)
    recent_turns = _turns(source.get("recent_turns"), policy)
    tasks = _tasks(source.get("recent_tasks"), policy)
    files = _files(source.get("relevant_files"), policy.file_limit)
    locators = _locators(source.get("recent_locators"), policy.locator_limit)
    values = {
        "active_task": _task(source.get("active_task"), policy),
        "recent_turns": recent_turns,
        "conversation_summary": summary,
        "recent_tasks": tasks,
        "candidate_entities": _records(candidates if candidates is not None else source.get("candidate_entities"), policy.entity_limit, policy),
        "verified_facts": _records(facts if facts is not None else source.get("verified_facts"), policy.fact_limit, policy),
        "inferences": _records(inferences if inferences is not None else source.get("inferences"), policy.inference_limit, policy),
        "uncertainties": _records(uncertainties if uncertainties is not None else source.get("uncertainties"), policy.uncertainty_limit, policy),
        "relevant_files": files,
        "recent_locators": locators,
        "distillation": _distillation(summary, source.get("conversation_summary")),
    }
    source_counts = {
        "recent_turns": len(_list(source.get("recent_turns"))),
        "recent_tasks": len(_list(source.get("recent_tasks"))),
        "candidate_entities": len(candidates if candidates is not None else _list(source.get("candidate_entities"))),
        "verified_facts": len(facts if facts is not None else _list(source.get("verified_facts"))),
        "inferences": len(inferences if inferences is not None else _list(source.get("inferences"))),
        "uncertainties": len(uncertainties if uncertainties is not None else _list(source.get("uncertainties"))),
        "relevant_files": len(_list(source.get("relevant_files"))),
        "recent_locators": len(_list(source.get("recent_locators"))),
    }
    showing = {
        key: len(value)
        for key, value in values.items()
        if isinstance(value, list)
    }
    index = {
        "version": "memory-resolution-v1",
        "tier": policy.depth,
        "summary_available": bool(summary.get("summary")),
        "distillation_level": int(summary.get("distillation_level") or 0),
        "source_counts": source_counts,
        "showing": showing,
        "expansion": {
            "available": policy.depth != "deep",
            "next_tier": _next_depth(policy.depth),
            "request": {
                "kind": "memory_expand",
                "arguments": {"depth": _next_depth(policy.depth)},
            }
            if policy.depth != "deep"
            else None,
            "rule": (
                "Request expansion only when the index lacks a necessary fact, prior decision, "
                "or active-subject detail. Do not ask the user before checking the next memory tier."
            ),
        },
    }
    result = {**values, "memory_index": index}
    index["estimated_tokens"] = _estimate_tokens(result)
    return result


def _next_depth(depth: str) -> str | None:
    try:
        return _DEPTHS[_DEPTHS.index(depth) + 1]
    except IndexError:
        return None


def _estimate_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    head = max(40, limit * 2 // 3)
    tail = max(20, limit - head - 22)
    return text[:head] + " …[compacted]… " + text[-tail:]


def _summary(value: Any, policy: MemoryResolutionPolicy) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    return {
        "summary": _text(source.get("summary"), policy.summary_chars),
        "entities": _records(source.get("entities"), min(8, policy.entity_limit), policy),
        "relations": _records(source.get("relations"), min(8, policy.entity_limit), policy),
        "uncertainties": _records(source.get("uncertainties"), policy.uncertainty_limit, policy),
        "distillation_level": int(source.get("distillation_level") or 0),
        "updated_at": source.get("updated_at"),
        "source_message_id": source.get("source_message_id"),
    }


def _distillation(summary: dict[str, Any], raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}
    return {
        "level": int(summary.get("distillation_level") or 0),
        "summary_freshness": source.get("updated_at"),
        "background_upgrade_pending": int(summary.get("distillation_level") or 0) < 2,
        "source": "lh_conversation_summaries",
    }


def _turns(value: Any, policy: MemoryResolutionPolicy) -> list[dict[str, Any]]:
    turns = _list(value)[-policy.turn_limit :]
    result: list[dict[str, Any]] = []
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        user = turn.get("user") if isinstance(turn.get("user"), dict) else {}
        assistants = _list(turn.get("assistant"))[-1:]
        record: dict[str, Any] = {}
        if user:
            record["user"] = {
                "id": user.get("id"),
                "content": _text(user.get("content"), policy.item_chars),
                "created_at": user.get("created_at"),
            }
        if assistants:
            latest = assistants[-1] if isinstance(assistants[-1], dict) else {}
            if latest:
                record["assistant"] = {
                    "id": latest.get("id"),
                    "content": _text(latest.get("content"), policy.item_chars),
                    "created_at": latest.get("created_at"),
                }
        if record:
            result.append(record)
    return result


def _task(value: Any, policy: MemoryResolutionPolicy) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    # `active_task` is a public context shape. Keep its known keys even when
    # their value is null: callers distinguish an unset subject from a missing
    # field, and compacting memory must not silently change that contract.
    result: dict[str, Any] = {}
    for key in ("id", "goal", "status", "summary", "subject_kind", "subject", "subject_display", "updated_at"):
        raw = value.get(key)
        result[key] = (
            _text(raw, policy.item_chars)
            if key in {"goal", "summary", "subject", "subject_display"} and raw not in (None, "")
            else raw
        )
    return result


def _tasks(value: Any, policy: MemoryResolutionPolicy) -> list[dict[str, Any]]:
    return [item for item in (_task(item, policy) for item in _list(value)[: policy.task_limit]) if item]


def _files(value: Any, limit: int) -> list[dict[str, Any]]:
    return [
        {
            key: item.get(key)
            for key in ("canonical_path", "relative_path", "name", "extension", "content_hash", "last_seen_at", "last_opened_at")
            if item.get(key) not in (None, "")
        }
        for item in _list(value)[:limit]
        if isinstance(item, dict)
    ]


def _locators(value: Any, limit: int) -> list[dict[str, Any]]:
    return [
        {
            key: item.get(key)
            for key in ("kind", "canonical_value", "display_value", "label", "last_used_at", "use_count")
            if item.get(key) not in (None, "")
        }
        for item in _list(value)[:limit]
        if isinstance(item, dict)
    ]


def _records(value: Any, limit: int, policy: MemoryResolutionPolicy) -> list[Any]:
    result: list[Any] = []
    for item in _list(value)[:limit]:
        if isinstance(item, str):
            result.append(_text(item, policy.item_chars))
        elif isinstance(item, dict):
            result.append(_compact_record(item, policy))
    return result


def _compact_record(value: dict[str, Any], policy: MemoryResolutionPolicy) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, item in value.items():
        if key in {"evidence", "based_on"} and isinstance(item, list):
            record[key] = [_text(part, min(120, policy.item_chars)) for part in item[:3]]
        elif isinstance(item, str):
            record[key] = _text(item, policy.item_chars)
        elif isinstance(item, list):
            record[key] = [_text(part, min(120, policy.item_chars)) for part in item[:4]]
        elif isinstance(item, dict):
            record[key] = {
                str(subkey): _text(subvalue, min(160, policy.item_chars))
                for subkey, subvalue in list(item.items())[:8]
            }
        else:
            record[key] = item
    return record
