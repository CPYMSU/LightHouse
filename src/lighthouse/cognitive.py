from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any

from .engineering import StructuredOpenAICompatibleProvider
from .models import AgentRunStatus
from .provider import AgentDecision, parse_decision


_PHASES = {
    "understanding",
    "investigating",
    "finding",
    "decision",
    "implementing",
    "verifying",
    "recovering",
    "completing",
}
_VISIBILITY = {"focus", "balanced", "verbose"}
_IMPORTANCE = {"normal", "important", "critical"}
_TERMINAL = {
    AgentRunStatus.SUCCEEDED,
    AgentRunStatus.COMPLETED_WITH_WARNING,
    AgentRunStatus.PARTIALLY_COMPLETED,
    AgentRunStatus.FAILED,
    AgentRunStatus.CANCELLED,
}
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}\b"), "[REDACTED]"),
    (
        re.compile(r"(?i)((?:api[_-]?key|password|passwd|secret|access[_-]?token)\s*[:=]\s*)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(://[^:/\s]+:)[^@/\s]+(@)"), r"\1[REDACTED]\2"),
)


@dataclass(frozen=True)
class CognitiveAgentDecision(AgentDecision):
    """Normal agent decision with optional safe user-facing cognition metadata."""

    display: dict[str, Any] | None = None
    cognitive_delta: dict[str, Any] | None = None

    def public_dict(self) -> dict[str, Any]:
        value = super().public_dict()
        value["display"] = self.display
        value["cognitive_delta"] = self.cognitive_delta
        return value


def _redact_text(value: Any, limit: int = 1200) -> str:
    text = str(value if value is not None else "")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def _public_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 5:
        return "[compacted]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:32]:
            name = _redact_text(key, 80)
            if any(
                term in name.lower()
                for term in ("api_key", "password", "secret", "authorization", "access_token")
            ):
                result[name] = "[REDACTED]"
            else:
                result[name] = _public_value(item, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple, set)):
        return [_public_value(item, depth=depth + 1) for item in list(value)[:24]]
    return _redact_text(value)


def _normalise_display(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    phase = str(value.get("phase") or "investigating").strip().lower()
    if phase not in _PHASES:
        phase = "investigating"
    title = _redact_text(value.get("title") or value.get("headline") or phase.title(), 120).strip()
    summary = _redact_text(value.get("summary") or "", 700).strip()
    details = [
        _redact_text(item, 320).strip()
        for item in list(value.get("details") or [])[:8]
        if str(item or "").strip()
    ]
    evidence = []
    for item in list(value.get("evidence") or [])[:8]:
        if isinstance(item, dict):
            evidence.append(
                {
                    key: _redact_text(item.get(key), 240)
                    for key in ("type", "path", "symbol", "label", "status")
                    if item.get(key) not in {None, ""}
                }
            )
        elif str(item or "").strip():
            evidence.append({"label": _redact_text(item, 240)})
    importance = str(value.get("importance") or "normal").lower()
    visibility = str(value.get("visibility") or "balanced").lower()
    if importance not in _IMPORTANCE:
        importance = "normal"
    if visibility not in _VISIBILITY:
        visibility = "balanced"
    if not title and not summary:
        return None
    return {
        "phase": phase,
        "title": title or phase.title(),
        "summary": summary,
        "details": details,
        "evidence": evidence,
        "importance": importance,
        "visibility": visibility,
    }


def _normalise_delta(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    allowed = {
        "goal",
        "understanding",
        "strategy",
        "active_work",
        "work_items",
        "completed",
        "open_questions",
        "next_intent",
        "verified_facts",
        "assumptions",
        "decisions",
    }
    result = {key: _public_value(value[key]) for key in allowed if key in value}
    return result or None


def parse_cognitive_decision(value: Any) -> CognitiveAgentDecision:
    base = parse_decision(value)
    display = _normalise_display(value.get("display")) if isinstance(value, dict) else None
    delta = _normalise_delta(value.get("cognitive_delta")) if isinstance(value, dict) else None
    return CognitiveAgentDecision(
        kind=base.kind,
        reason=base.reason,
        capability=base.capability,
        arguments=base.arguments,
        message=base.message,
        display=display,
        cognitive_delta=delta,
    )


class CognitiveStructuredProvider(StructuredOpenAICompatibleProvider):
    """Structured provider that preserves safe cognition metadata on decisions."""

    def decide(self, *, system_prompt: str, state: dict[str, Any]) -> CognitiveAgentDecision:
        state_text = self._bounded_json(state)
        run = state.get("run") if isinstance(state.get("run"), dict) else {}
        context = state.get("usage_context") if isinstance(state.get("usage_context"), dict) else {}
        usage_context = {
            **context,
            "workspace_id": context.get("workspace_id") or run.get("workspace_id"),
            "run_id": context.get("run_id") or run.get("id"),
            "call_kind": "main_ai",
        }
        value = self._json_completion(
            system_prompt=system_prompt,
            user_content=(
                "Continue the LightHouse run from this durable state. "
                "Return exactly one decision object. Include display or cognitive_delta only "
                "when they add meaningful continuity for the user and your next turn.\n"
                + state_text
            ),
            usage_context=usage_context,
        )
        return parse_cognitive_decision(value)


def _merge_dict(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key, value in update.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_dict(target[key], value)
        elif isinstance(value, list) and isinstance(target.get(key), list):
            combined = [*target[key], *value]
            unique: list[Any] = []
            fingerprints: set[str] = set()
            for item in combined:
                fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
                if fingerprint in fingerprints:
                    continue
                fingerprints.add(fingerprint)
                unique.append(item)
            target[key] = unique[-40:]
        else:
            target[key] = value


def _patch_paths(arguments: dict[str, Any]) -> list[str]:
    patch = str(arguments.get("patch") or "")
    values = []
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if path and path != "/dev/null":
                values.append(path)
    return sorted(set(values))


def _activity_for_decision(sequence: int, payload: dict[str, Any]) -> dict[str, Any] | None:
    if payload.get("kind") != "tool":
        return None
    capability = str(payload.get("capability") or "")
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    label = "TOOL"
    summary = capability
    importance = "normal"
    if capability == "system.file.read.v1":
        label, summary = "READ", str(arguments.get("path") or "project file")
    elif capability == "system.file.search.v1":
        label = "SEARCH"
        summary = str(arguments.get("query") or "code")
        if arguments.get("path"):
            summary += " in " + str(arguments.get("path"))
    elif capability == "system.file.patch.v1":
        paths = _patch_paths(arguments)
        label, importance = "EDIT", "important"
        summary = ", ".join(paths[:6]) or "apply code patch"
    elif capability == "system.test.run.v1":
        label, importance = "TEST", "important"
        summary = str(arguments.get("command") or "project tests")
    elif capability == "system.git.diff.v1":
        label, summary, importance = "DIFF", "review current Git diff", "important"
    elif capability == "system.git.status.v1":
        label, summary = "GIT", "inspect repository status"
    elif capability == "system.project.context.v1":
        label, summary = "CONTEXT", "index project files and instructions"
    elif capability.startswith("data.sql.query"):
        label, summary = "DATABASE", "inspect PostgreSQL data"
    elif capability.startswith("data.sql.exec"):
        label, summary, importance = "DATABASE", "execute PostgreSQL transaction", "important"
    elif capability.startswith("system.service"):
        label, importance = "SERVICE", "important"
        summary = str(arguments.get("service") or capability)
    elif capability.startswith("agent.bus"):
        label, summary = "AGENT", str(arguments.get("role") or arguments.get("goal") or capability)
    elif capability.startswith("desktop."):
        label, summary = "DESKTOP", str(arguments.get("url") or arguments.get("path") or capability)
    elif capability == "system.shell.exec.v1":
        label, summary, importance = "EXEC", str(arguments.get("command") or "shell command"), "important"
    return {
        "sequence": sequence,
        "label": label,
        "summary": _redact_text(summary, 360),
        "capability": capability,
        "status": "requested",
        "importance": importance,
    }


def _activity_for_observation(sequence: int, payload: dict[str, Any]) -> dict[str, Any]:
    capability = str(payload.get("capability") or "operation")
    receipt = payload.get("receipt") if isinstance(payload.get("receipt"), dict) else {}
    ok = receipt.get("ok")
    result = receipt.get("result") if receipt else None
    return {
        "sequence": sequence,
        "label": "PASS" if ok is True else "FAIL" if ok is False else "RESULT",
        "summary": _redact_text(result, 360),
        "capability": capability,
        "status": "succeeded" if ok is True else "failed" if ok is False else "observed",
        "importance": "critical" if ok is False else "important" if capability in {
            "system.test.run.v1",
            "system.git.diff.v1",
            "system.file.patch.v1",
        } else "normal",
    }


def _error_update(sequence: int, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "sequence": sequence,
        "phase": "recovering",
        "title": kind.replace("_", " ").title(),
        "summary": _redact_text(payload.get("error") or payload.get("message") or payload, 700),
        "details": [],
        "evidence": [],
        "importance": "critical",
        "visibility": "focus",
    }


def build_cognitive_observer(run: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {
        "goal": {"summary": _redact_text(run.get("task") or "", 800), "success_criteria": []},
        "understanding": {"current": "", "confidence": None},
        "strategy": {"current": "", "reason": ""},
        "active_work": {
            "stage": "understanding",
            "headline": "Understanding the requested outcome",
            "active_files": [],
            "changed_files": [],
        },
        "work_items": [],
        "completed": [],
        "open_questions": [],
        "next_intent": "",
        "verified_facts": [],
        "assumptions": [],
        "decisions": [],
        "user_directions": [],
        "validation": {"passed": 0, "failed": 0, "running": 0, "latest": []},
        "last_sequence": 0,
    }
    timeline: list[dict[str, Any]] = []
    activity: list[dict[str, Any]] = []
    changed_files: set[str] = set()

    for step in steps:
        if not isinstance(step, dict):
            continue
        sequence = int(step.get("sequence") or 0)
        kind = str(step.get("kind") or "")
        payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
        state["last_sequence"] = max(int(state.get("last_sequence") or 0), sequence)

        if kind == "run_created":
            timeline.append(
                {
                    "sequence": sequence,
                    "phase": "understanding",
                    "title": "Task accepted",
                    "summary": _redact_text(payload.get("task") or run.get("task") or "", 700),
                    "details": [],
                    "evidence": [],
                    "importance": "important",
                    "visibility": "balanced",
                }
            )
        elif kind == "decision":
            delta = _normalise_delta(payload.get("cognitive_delta"))
            if delta:
                _merge_dict(state, delta)
            display = _normalise_display(payload.get("display"))
            if display:
                event = {"sequence": sequence, **display}
                timeline.append(event)
                state["active_work"]["stage"] = display["phase"]
                state["active_work"]["headline"] = display["title"]
                if display["phase"] in {"understanding", "finding"} and display["summary"]:
                    state["understanding"]["current"] = display["summary"]
                if display["phase"] == "decision" and display["summary"]:
                    state["strategy"]["current"] = display["summary"]
            item = _activity_for_decision(sequence, payload)
            if item:
                activity.append(item)
                if item["capability"] == "system.file.patch.v1":
                    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
                    changed_files.update(_patch_paths(arguments))
                if item["capability"] == "system.test.run.v1":
                    state["validation"]["running"] = 1
        elif kind in {"user_direction", "user_input"}:
            message = _redact_text(payload.get("message") or "", 900).strip()
            if message:
                state["user_directions"].append(message)
                state["user_directions"] = state["user_directions"][-12:]
                timeline.append(
                    {
                        "sequence": sequence,
                        "phase": "decision",
                        "title": "User direction received",
                        "summary": message,
                        "details": [],
                        "evidence": [],
                        "importance": "critical",
                        "visibility": "focus",
                    }
                )
        elif kind == "observation":
            item = _activity_for_observation(sequence, payload)
            activity.append(item)
            capability = item["capability"]
            if capability == "system.test.run.v1":
                state["validation"]["running"] = 0
                key = "passed" if item["status"] == "succeeded" else "failed"
                state["validation"][key] = int(state["validation"].get(key) or 0) + 1
                state["validation"]["latest"].append(item)
                state["validation"]["latest"] = state["validation"]["latest"][-8:]
            if item["status"] == "failed":
                state["active_work"]["stage"] = "recovering"
                state["active_work"]["headline"] = "Recovering from a verified failure"
                timeline.append(
                    {
                        "sequence": sequence,
                        "phase": "recovering",
                        "title": "Verified operation failed",
                        "summary": item["summary"],
                        "details": [capability],
                        "evidence": [{"type": "receipt", "status": "failed"}],
                        "importance": "critical",
                        "visibility": "focus",
                    }
                )
        elif kind == "completion_review":
            status = str(payload.get("status") or "")
            if status == "revise":
                state["active_work"]["stage"] = "verifying"
                state["active_work"]["headline"] = "Completion evidence needs revision"
                timeline.append(
                    {
                        "sequence": sequence,
                        "phase": "verifying",
                        "title": "Completion review requested more evidence",
                        "summary": "; ".join(
                            str(item) for item in [*(payload.get("blockers") or []), *(payload.get("guidance") or [])]
                        ),
                        "details": [],
                        "evidence": [],
                        "importance": "important",
                        "visibility": "balanced",
                    }
                )
        elif kind == "input_required":
            timeline.append(
                {
                    "sequence": sequence,
                    "phase": "decision",
                    "title": "User input required",
                    "summary": _redact_text(payload.get("message") or "", 700),
                    "details": [],
                    "evidence": [],
                    "importance": "critical",
                    "visibility": "focus",
                }
            )
        elif kind in {
            "protocol_error",
            "provider_error",
            "tool_rejected",
            "address_rejected",
            "run_failed",
        }:
            state["active_work"]["stage"] = "recovering"
            state["active_work"]["headline"] = "Recovering from a runtime error"
            timeline.append(_error_update(sequence, kind, payload))
        elif kind == "budget_extended":
            timeline.append(
                {
                    "sequence": sequence,
                    "phase": "implementing",
                    "title": "Engineering budget extended",
                    "summary": f"Useful progress continues; model budget extended to {payload.get('new_limit')} steps.",
                    "details": [],
                    "evidence": [],
                    "importance": "normal",
                    "visibility": "verbose",
                }
            )
        elif kind == "run_completed":
            state["active_work"]["stage"] = "completing"
            state["active_work"]["headline"] = "Receipt-backed work completed"

    state["active_work"]["changed_files"] = sorted(changed_files)
    if changed_files and not state["active_work"].get("active_files"):
        state["active_work"]["active_files"] = sorted(changed_files)[:12]
    state["user_directions"] = list(dict.fromkeys(state["user_directions"]))[-12:]
    timeline = timeline[-80:]
    activity = activity[-120:]
    return {
        "state": _public_value(state),
        "timeline": _public_value(timeline),
        "activity": _public_value(activity),
        "last_sequence": int(state.get("last_sequence") or 0),
    }


class CognitiveContinuityMixin:
    """Expose one durable cognitive state to both the main AI and the user."""

    def snapshot(self, run_id: str) -> dict[str, Any]:
        snapshot = super().snapshot(run_id)
        run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
        steps = snapshot.get("steps") if isinstance(snapshot.get("steps"), list) else []
        snapshot["cognitive_observer"] = build_cognitive_observer(run, steps)
        return snapshot

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        run = state.get("run") if isinstance(state.get("run"), dict) else {}
        steps = state.get("steps") if isinstance(state.get("steps"), list) else self.repository.list_agent_steps(run_id)
        observer = build_cognitive_observer(run, steps)
        continuity = {
            "state": observer["state"],
            "recent_updates": list(observer["timeline"])[-14:],
            "recent_activity": list(observer["activity"])[-18:],
            "instruction": (
                "Continue from this state instead of reconstructing the task from raw history. "
                "Verified facts outrank assumptions; user directions outrank previous strategy."
            ),
        }
        state["cognitive_continuity"] = continuity
        engineering = state.setdefault("engineering", {})
        if isinstance(engineering, dict):
            engineering["cognitive_continuity"] = continuity
        return state

    def _system_prompt(self, run) -> str:
        base = super()._system_prompt(run)
        return (
            "You maintain a user-visible Cognitive Continuity record. Do not reveal private chain-of-thought, "
            "hidden scratch work, secrets or raw internal deliberation. Instead, when useful, add an optional "
            "display object containing phase, title, summary, details, evidence, importance and visibility. "
            "Its content must be concise, conclusion-oriented and safe to show: what you understand, what changed, "
            "why the current engineering direction was chosen, what failed, or what comes next. Avoid repetitive "
            "status filler and omit display for routine low-value steps. You may also add an optional cognitive_delta "
            "object updating goal, understanding, strategy, active_work, work_items, completed, open_questions, "
            "next_intent, verified_facts, assumptions or decisions. Facts require evidence; uncertain ideas belong "
            "under assumptions or open_questions. When the later base prompt lists tool, final or ask decision "
            "objects, display and cognitive_delta remain optional additional fields on those same objects. "
            + base
        )

    def advance(self, run_id: str) -> dict[str, Any]:
        before = self.repository.get_agent_run(run_id)
        preserved_auto = bool(before.auto_confirm)
        preserved_scope = dict(before.auto_scope or {})
        snapshot = super().advance(run_id)
        run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
        if preserved_auto and run.get("status") == AgentRunStatus.WAITING_INPUT.value:
            self.repository.update_agent_run(
                run_id,
                auto_confirm=True,
                auto_scope=preserved_scope,
            )
            snapshot = self.snapshot(run_id)
        return snapshot

    def provide_input(self, run_id: str, *, actor: str, message: str) -> dict[str, Any]:
        run = self.repository.get_agent_run(run_id)
        if run.actor != actor:
            raise PermissionError("only the agent run actor may provide input")
        message = str(message or "").strip()
        if not message:
            raise ValueError("input message is required")
        if run.status != AgentRunStatus.WAITING_INPUT:
            raise ValueError(f"agent run is not waiting for input: {run.status.value}")
        self.repository.append_agent_step(run_id, "user_input", {"actor": actor, "message": message})
        self.repository.update_agent_run(
            run_id,
            status=AgentRunStatus.RUNNING,
            final_message=None,
            response_status="pending",
            goal_status="in_progress",
            warning=None,
        )
        return self.advance(run_id)

    def provide_direction(self, run_id: str, *, actor: str, message: str) -> dict[str, Any]:
        run = self.repository.get_agent_run(run_id)
        if run.actor != actor:
            raise PermissionError("only the agent run actor may steer it")
        message = str(message or "").strip()
        if not message:
            raise ValueError("direction message is required")
        if run.status in _TERMINAL:
            raise ValueError(f"agent run is already terminal: {run.status.value}")
        if run.status == AgentRunStatus.WAITING_INPUT:
            return self.provide_input(run_id, actor=actor, message=message)
        self.repository.append_agent_step(
            run_id,
            "user_direction",
            {
                "actor": actor,
                "message": message,
                "requires_replan": True,
                "status_when_received": run.status.value,
            },
        )
        return self.snapshot(run_id)
