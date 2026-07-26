from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .agent import _TERMINAL_AGENT_STATES


_INTENSITIES = {"quick", "balanced", "advanced", "extreme"}


@dataclass(frozen=True)
class IntensityPolicy:
    name: str
    quality_priority: float
    latency_priority: float
    cost_tolerance: float
    reasoning_effort: str
    context_depth: str
    initial_main_steps: int
    hard_main_steps: int
    agent_initial_rounds: int
    agent_hard_rounds: int
    agent_extension_rounds: int
    parallelism_hint: int
    verification_depth: str
    independent_review: str
    full_regression: str
    collaboration_depth: int

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "quality_priority": self.quality_priority,
            "latency_priority": self.latency_priority,
            "cost_tolerance": self.cost_tolerance,
            "reasoning_effort": self.reasoning_effort,
            "context_depth": self.context_depth,
            "initial_main_steps": self.initial_main_steps,
            "hard_main_steps": self.hard_main_steps,
            "agent_initial_rounds": self.agent_initial_rounds,
            "agent_hard_rounds": self.agent_hard_rounds,
            "agent_extension_rounds": self.agent_extension_rounds,
            "parallelism_hint": self.parallelism_hint,
            "verification_depth": self.verification_depth,
            "independent_review": self.independent_review,
            "full_regression": self.full_regression,
            "collaboration_depth": self.collaboration_depth,
        }


POLICIES: dict[str, IntensityPolicy] = {
    "quick": IntensityPolicy(
        name="quick",
        quality_priority=0.55,
        latency_priority=0.95,
        cost_tolerance=0.25,
        reasoning_effort="low",
        context_depth="targeted",
        initial_main_steps=16,
        hard_main_steps=48,
        agent_initial_rounds=4,
        agent_hard_rounds=12,
        agent_extension_rounds=4,
        parallelism_hint=1,
        verification_depth="targeted",
        independent_review="off",
        full_regression="off_unless_risk",
        collaboration_depth=0,
    ),
    "balanced": IntensityPolicy(
        name="balanced",
        quality_priority=0.75,
        latency_priority=0.65,
        cost_tolerance=0.55,
        reasoning_effort="medium",
        context_depth="related_call_path",
        initial_main_steps=48,
        hard_main_steps=128,
        agent_initial_rounds=8,
        agent_hard_rounds=20,
        agent_extension_rounds=4,
        parallelism_hint=3,
        verification_depth="proportional",
        independent_review="risk_based",
        full_regression="risk_based",
        collaboration_depth=1,
    ),
    "advanced": IntensityPolicy(
        name="advanced",
        quality_priority=0.9,
        latency_priority=0.35,
        cost_tolerance=0.8,
        reasoning_effort="high",
        context_depth="deep",
        initial_main_steps=64,
        hard_main_steps=256,
        agent_initial_rounds=8,
        agent_hard_rounds=32,
        agent_extension_rounds=4,
        parallelism_hint=6,
        verification_depth="extended",
        independent_review="normally_on",
        full_regression="risk_based",
        collaboration_depth=2,
    ),
    "extreme": IntensityPolicy(
        name="extreme",
        quality_priority=1.0,
        latency_priority=0.1,
        cost_tolerance=1.0,
        reasoning_effort="max",
        context_depth="global_on_demand",
        initial_main_steps=96,
        hard_main_steps=512,
        agent_initial_rounds=12,
        agent_hard_rounds=64,
        agent_extension_rounds=4,
        parallelism_hint=12,
        verification_depth="comprehensive",
        independent_review="required",
        full_regression="normally_on",
        collaboration_depth=3,
    ),
}


def normalize_intensity(value: Any) -> str:
    name = str(value or "balanced").strip().lower()
    aliases = {
        "fast": "quick",
        "normal": "balanced",
        "high": "advanced",
        "max": "extreme",
        "pro": "extreme",
        "快速": "quick",
        "平衡": "balanced",
        "高级": "advanced",
        "高級": "advanced",
        "极致": "extreme",
        "極致": "extreme",
    }
    name = aliases.get(name, name)
    if name not in _INTENSITIES:
        raise ValueError("work intensity must be quick, balanced, advanced or extreme")
    return name


def resolve_intensity(value: Any) -> IntensityPolicy:
    return POLICIES[normalize_intensity(value)]


def intensity_from_steps(steps: list[dict[str, Any]]) -> str:
    selected = "balanced"
    for step in steps:
        if not isinstance(step, dict):
            continue
        payload = step.get("payload") if isinstance(step.get("payload"), dict) else {}
        if step.get("kind") == "run_created" and payload.get("work_intensity"):
            selected = normalize_intensity(payload["work_intensity"])
        elif step.get("kind") == "intensity_changed" and payload.get("to"):
            selected = normalize_intensity(payload["to"])
    return selected


def policy_for_run(repository: Any, run_id: str) -> IntensityPolicy:
    return resolve_intensity(intensity_from_steps(repository.list_agent_steps(run_id)))


class WorkIntensityMixin:
    """Resource and evidence preferences without imposing a fixed workflow."""

    minimum_soft_steps = 1
    hard_step_limit = 512
    extension_size = 16

    def start(
        self,
        *,
        task: str,
        workspace_id: str,
        actor: str,
        mode=None,
        max_steps: int | None = None,
        auto_confirm: bool = False,
        work_intensity: str = "balanced",
        **kwargs: Any,
    ) -> dict[str, Any]:
        policy = resolve_intensity(work_intensity)
        requested = policy.initial_main_steps if max_steps is None else int(max_steps)
        seed_steps = min(64, max(1, requested, min(policy.initial_main_steps, 64)))
        values = {
            "task": task,
            "workspace_id": workspace_id,
            "actor": actor,
            "max_steps": seed_steps,
            "auto_confirm": auto_confirm,
            **kwargs,
        }
        if mode is not None:
            values["mode"] = mode
        snapshot = super().start(**values)
        run = snapshot.get("run") if isinstance(snapshot.get("run"), dict) else {}
        run_id = str(run.get("id") or "")
        if run_id and intensity_from_steps(self.repository.list_agent_steps(run_id)) != policy.name:
            self.repository.append_agent_step(
                run_id,
                "intensity_changed",
                {
                    "from": "balanced",
                    "to": policy.name,
                    "reason": "run_started",
                    "effective_from_sequence": len(self.repository.list_agent_steps(run_id)) + 1,
                },
            )
            snapshot = self.snapshot(run_id)
        return snapshot

    def set_work_intensity(self, run_id: str, *, actor: str, intensity: str) -> dict[str, Any]:
        run = self.repository.get_agent_run(run_id)
        if run.actor != actor:
            raise PermissionError("only the agent run actor may change work intensity")
        if run.status in _TERMINAL_AGENT_STATES:
            raise ValueError(f"agent run is already terminal: {run.status.value}")
        next_name = normalize_intensity(intensity)
        previous = intensity_from_steps(self.repository.list_agent_steps(run_id))
        if previous != next_name:
            self.repository.append_agent_step(
                run_id,
                "intensity_changed",
                {
                    "from": previous,
                    "to": next_name,
                    "reason": "user_requested",
                    "effective_from_sequence": len(self.repository.list_agent_steps(run_id)) + 1,
                },
            )
        return self.snapshot(run_id)

    def snapshot(self, run_id: str) -> dict[str, Any]:
        snapshot = super().snapshot(run_id)
        policy = policy_for_run(self.repository, run_id)
        snapshot["work_intensity"] = {
            "selected": policy.name,
            "effective": policy.public_dict(),
            "adaptations": self._intensity_adaptations(run_id),
        }
        observer = snapshot.get("cognitive_observer")
        if isinstance(observer, dict) and isinstance(observer.get("state"), dict):
            observer["state"]["work_intensity"] = snapshot["work_intensity"]
        return snapshot

    def _model_state(self, run_id: str) -> dict[str, Any]:
        state = super()._model_state(run_id)
        policy = policy_for_run(self.repository, run_id)
        state["work_intensity"] = {
            "selected": policy.name,
            "effective": policy.public_dict(),
            "adaptations": self._intensity_adaptations(run_id),
            "instruction": (
                "This is a resource and quality preference, not a fixed workflow. "
                "Use less work when the task is genuinely simple and increase only the risk-relevant dimensions."
            ),
        }
        continuity = state.get("cognitive_continuity")
        if isinstance(continuity, dict) and isinstance(continuity.get("state"), dict):
            continuity["state"]["work_intensity"] = state["work_intensity"]
        return state

    def _system_prompt(self, run) -> str:
        policy = policy_for_run(self.repository, run.id)
        return (
            f"The user selected {policy.name.upper()} work intensity. Treat it as a preference vector: "
            f"reasoning={policy.reasoning_effort}, context={policy.context_depth}, "
            f"verification={policy.verification_depth}, independent_review={policy.independent_review}, "
            f"agent_parallelism_hint={policy.parallelism_hint}. Do not manufacture work to consume the budget. "
            "For a simple isolated change remain efficient; for security, authorization, data migration, public "
            "contracts or production release, adapt the relevant verification dimensions upward and explain that "
            "adaptation through Cognitive Continuity. "
            + super()._system_prompt(run)
        )

    def _effective_step_limit(self, run_id: str, initial: int) -> int:
        policy = policy_for_run(self.repository, run_id)
        base = super()._effective_step_limit(run_id, initial)
        return min(policy.hard_main_steps, max(base, policy.initial_main_steps))

    def _may_extend(self, run_id: str) -> bool:
        run = self.repository.get_agent_run(run_id)
        policy = policy_for_run(self.repository, run_id)
        if run.current_step >= policy.hard_main_steps:
            return False
        return super()._may_extend(run_id)

    def _intensity_adaptations(self, run_id: str) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []
        for step in self.repository.list_agent_steps(run_id):
            if step.get("kind") != "intensity_adapted" or not isinstance(step.get("payload"), dict):
                continue
            values.append(dict(step["payload"]))
        return values[-20:]
