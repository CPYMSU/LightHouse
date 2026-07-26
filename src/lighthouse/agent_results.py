from __future__ import annotations

from typing import Any

from .cognitive import CognitiveStructuredProvider


def _unique(values: list[Any], limit: int = 80) -> list[Any]:
    result: list[Any] = []
    fingerprints: set[str] = set()
    import json

    for item in values:
        fingerprint = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        result.append(item)
    return result[-limit:]


def _finding(item: Any, *, default_status: str = "proposed") -> dict[str, Any] | None:
    if isinstance(item, str):
        claim = item.strip()
        if not claim:
            return None
        return {"claim": claim, "status": default_status, "confidence": 0.6, "evidence": []}
    if not isinstance(item, dict):
        return None
    claim = str(item.get("claim") or item.get("finding") or item.get("summary") or "").strip()
    if not claim:
        return None
    status = str(item.get("status") or default_status).strip().lower()
    if status not in {"proposed", "verified", "contradicted", "superseded", "accepted", "rejected"}:
        status = default_status
    return {
        "claim": claim,
        "status": status,
        "confidence": max(0.0, min(float(item.get("confidence") or 0.6), 1.0)),
        "evidence": list(item.get("evidence") or [])[:20],
        "metadata": {
            key: value
            for key, value in item.items()
            if key not in {"claim", "finding", "summary", "status", "confidence", "evidence"}
        },
    }


def normalise_agent_result(
    *,
    agent: dict[str, Any],
    work_order: dict[str, Any],
    result: dict[str, Any],
    tool_results: list[dict[str, Any]],
) -> dict[str, Any]:
    payload = work_order.get("payload") if isinstance(work_order.get("payload"), dict) else {}
    assignment = payload.get("assignment") if isinstance(payload.get("assignment"), dict) else {}
    profile = str(
        assignment.get("execution_profile")
        or (agent.get("metadata") or {}).get("execution_profile")
        or "advisory"
    )
    result_type = str(result.get("result_type") or profile)
    if result_type not in {"advisory", "implementation", "verification", "integration", "release"}:
        result_type = "advisory"
    findings = []
    for item in list(result.get("findings") or [])[:60]:
        value = _finding(item)
        if value:
            findings.append(value)
    receipts = [
        item.get("receipt")
        for item in tool_results
        if isinstance(item, dict) and isinstance(item.get("receipt"), dict)
    ]
    changed_files: list[str] = []
    tests: list[dict[str, Any]] = []
    completion_evidence: list[dict[str, Any]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        capability = str(item.get("capability") or "")
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        if capability == "system.file.patch.v1":
            patch = str(arguments.get("patch") or "")
            for line in patch.splitlines():
                if line.startswith("+++ b/"):
                    path = line[6:].strip()
                    if path and path != "/dev/null" and path not in changed_files:
                        changed_files.append(path)
        elif capability.startswith("system.file.write"):
            path = str(arguments.get("path") or "").strip()
            if path and path not in changed_files:
                changed_files.append(path)
        if capability == "system.test.run.v1":
            tests.append(
                {
                    "command": arguments.get("command") or "project tests",
                    "status": "passed" if item.get("ok") else "failed",
                    "receipt": item.get("receipt"),
                }
            )
        if isinstance(item.get("receipt"), dict):
            completion_evidence.append(
                {
                    "capability": capability,
                    "ok": bool(item.get("receipt", {}).get("ok")),
                    "operation_id": (item.get("operation") or {}).get("id"),
                    "result_hash": item.get("receipt", {}).get("result_hash"),
                }
            )
    open_questions = list(result.get("open_questions") or result.get("uncertainties") or [])[:40]
    recommendations = list(result.get("recommendations") or [])[:40]
    risks = list(result.get("risks") or [])[:40]
    claims_checked = list(result.get("claims_checked") or [])[:40]
    passed = list(result.get("passed") or [])[:40]
    failed = list(result.get("failed") or [])[:40]
    unverified = list(result.get("unverified") or [])[:40]
    structured = {
        **result,
        "result_type": result_type,
        "summary": str(result.get("summary") or work_order.get("goal") or "Specialist result ready"),
        "findings": findings,
        "recommendations": recommendations,
        "risks": risks,
        "open_questions": open_questions,
        "changed_files": _unique([*list(result.get("changed_files") or []), *changed_files]),
        "tests": _unique([*list(result.get("tests") or []), *tests]),
        "claims_checked": claims_checked,
        "passed": passed,
        "failed": failed,
        "unverified": unverified,
        "receipts": receipts[-40:],
        "completion_evidence": _unique(
            [*list(result.get("completion_evidence") or []), *completion_evidence]
        ),
        "public_contract_changes": list(result.get("public_contract_changes") or [])[:30],
        "migration_required": bool(result.get("migration_required")),
        "remaining_risks": list(result.get("remaining_risks") or risks)[:40],
        "tool_results": tool_results[-60:],
        "work_order_id": work_order.get("id"),
        "agent_id": agent.get("id"),
        "role": work_order.get("role"),
        "execution_profile": profile,
    }
    return structured


def fuse_agent_results(state: dict[str, Any], work_orders: list[dict[str, Any]]) -> dict[str, Any]:
    state = dict(state or {})
    verified = list(state.get("verified_facts") or [])
    assumptions = list(state.get("assumptions") or [])
    open_questions = list(state.get("open_questions") or [])
    completed = list(state.get("completed") or [])
    conflicts = list(state.get("conflicts") or [])
    agent_results: list[dict[str, Any]] = []
    for work in work_orders:
        if not isinstance(work, dict):
            continue
        result = work.get("result") if isinstance(work.get("result"), dict) else {}
        if not result:
            continue
        agent_results.append(
            {
                "work_order_id": work.get("id"),
                "role": work.get("role"),
                "status": work.get("status"),
                "result_type": result.get("result_type"),
                "summary": result.get("summary"),
                "changed_files": result.get("changed_files") or [],
                "tests": result.get("tests") or [],
            }
        )
        for item in result.get("findings") or []:
            if not isinstance(item, dict):
                continue
            enriched = {**item, "source_agent": work.get("role"), "work_order_id": work.get("id")}
            if item.get("status") in {"verified", "accepted"}:
                verified.append(enriched)
            else:
                assumptions.append(enriched)
        for item in result.get("open_questions") or []:
            open_questions.append(
                {
                    "question": str(item),
                    "source_agent": work.get("role"),
                    "work_order_id": work.get("id"),
                }
            )
        if work.get("status") == "succeeded" and result.get("result_type") in {
            "implementation",
            "integration",
            "release",
        }:
            completed.append(
                {
                    "summary": result.get("summary"),
                    "role": work.get("role"),
                    "work_order_id": work.get("id"),
                    "changed_files": result.get("changed_files") or [],
                }
            )
        payload = work.get("payload") if isinstance(work.get("payload"), dict) else {}
        coordination = payload.get("coordination") if isinstance(payload.get("coordination"), dict) else {}
        for item in coordination.get("conflicts") or []:
            conflicts.append({**item, "work_order_id": work.get("id")})
    state["verified_facts"] = _unique(verified, 80)
    state["assumptions"] = _unique(assumptions, 80)
    state["open_questions"] = _unique(open_questions, 60)
    state["completed"] = _unique(completed, 60)
    state["conflicts"] = _unique(conflicts, 40)
    state["agent_results"] = agent_results[-40:]
    return state


class AgentResultFusionMixin:
    """Fuse completed specialist artifacts into the main AI's current cognition."""

    def snapshot(self, run_id: str) -> dict[str, Any]:
        snapshot = super().snapshot(run_id)
        observer = snapshot.get("cognitive_observer")
        work_orders = snapshot.get("work_orders") if isinstance(snapshot.get("work_orders"), list) else []
        if isinstance(observer, dict) and isinstance(observer.get("state"), dict):
            observer["state"] = fuse_agent_results(observer["state"], work_orders)
        snapshot["agent_result_fusion"] = {
            "completed_results": sum(1 for item in work_orders if item.get("result")),
            "work_order_count": len(work_orders),
        }
        return snapshot

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        run = self.repository.get_agent_run(run_id)
        work_orders: list[dict[str, Any]] = []
        agent_bus = getattr(self, "agent_bus", None)
        if agent_bus is not None:
            try:
                work_orders = agent_bus.list_work_orders(
                    workspace_id=run.workspace_id,
                    parent_run_id=run_id,
                    limit=100,
                )
            except Exception:
                work_orders = []
        continuity = state.get("cognitive_continuity")
        if isinstance(continuity, dict) and isinstance(continuity.get("state"), dict):
            continuity["state"] = fuse_agent_results(continuity["state"], work_orders)
        state["agent_results"] = [item.get("result") for item in work_orders if item.get("result")][-30:]
        return state


class AgentBusStructuredProvider(CognitiveStructuredProvider):
    """Provider prompt contract for tool-using, structured specialist Agents."""

    @staticmethod
    def _specialist_prompt(role: str) -> str:
        base = CognitiveStructuredProvider._specialist_prompt(role)
        return (
            base
            + " Maintain a compact local cognitive_state with understanding, strategy, verified_facts, "
            "open_questions, active_files and next_intent. Return result_type appropriate to the assignment "
            "profile (advisory, implementation, verification, integration or release). Implementation results "
            "must include changed_files, tests, public_contract_changes, migration_required, remaining_risks and "
            "completion_evidence. Verification results must include claims_checked, passed, failed, unverified and "
            "receipts. You may request collaboration through collaboration_requests containing role, goal, reason, "
            "scope and deliverables. Do not request duplicate work already present in shared_findings or the shared "
            "cognitive brief."
        )
