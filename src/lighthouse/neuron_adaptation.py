from __future__ import annotations

import json
from typing import Any, Mapping

from .neuron_model import StimulusVector, _clip
from .neuron_runtime import PostgresNeuronRuntime


def encode_database_event(
    *,
    event_type: str,
    operation: str,
    payload: Mapping[str, Any] | None = None,
) -> StimulusVector:
    """Encode nested PostgreSQL change payloads into animal-like reflex stimuli."""

    payload = payload or {}
    before_value = payload.get("before")
    after_value = payload.get("after")
    before = before_value if isinstance(before_value, Mapping) else {}
    after = after_value if isinstance(after_value, Mapping) else {}
    event = str(event_type or "database.change").strip().lower()
    operation = str(operation or "update").strip().lower()

    status = str(after.get("status") or "").strip().lower()
    if status:
        event = f"{event}.{status}"
    receipt_ok = after.get("ok")
    if receipt_ok is True:
        event = f"{event}.succeeded"
    elif receipt_ok is False:
        event = f"{event}.failed"

    changed_keys = payload.get("changed_keys")
    change_count = len(changed_keys) if isinstance(changed_keys, list) else len(payload)
    values: dict[str, float] = {
        "intensity": min(1.0, 0.12 + 0.03 * change_count),
        "recency": 1.0,
        "actionability": 0.25,
    }

    if operation in {"insert", "created", "create"}:
        values.update(novelty=0.55, information_gain=0.4)
    elif operation in {"update", "updated", "reinforced"}:
        values.update(memory_strength_delta=0.35, persistence=0.3)
    elif operation in {"delete", "deleted", "invalidated"}:
        values.update(
            loss_delta=0.75,
            threat=0.65,
            negative_valence=0.55,
            reversibility=-0.65,
        )

    if "memory" in event:
        values.update(
            memory_strength_delta=max(values.get("memory_strength_delta", 0.0), 0.5),
            association_growth=0.35,
            semantic_density=0.3,
        )
    if any(token in event for token in ("conflict", "contradiction", "invalid")):
        values.update(
            contradiction=0.8,
            conflict_level=0.75,
            uncertainty=0.55,
            negative_valence=0.35,
        )
    if any(
        token in event
        for token in ("success", "succeeded", "completed", "receipt.ok", "received")
    ):
        values.update(
            positive_valence=0.75,
            reward_delta=0.85,
            progress_delta=0.75,
            completion_signal=0.65,
            predictability=0.4,
        )
    if any(
        token in event
        for token in ("failure", "failed", "error", "receipt.failed", "cancelled")
    ):
        values.update(
            negative_valence=0.8,
            loss_delta=0.75,
            threat=0.75,
            failure_probability=0.8,
            urgency=0.6,
            recoverability=0.55,
        )
    if any(token in event for token in ("user", "message", "conversation")):
        values.update(
            user_source=0.75,
            relationship_relevance=0.45,
            emotional_intensity=0.25,
        )
    if str(after.get("role") or "").lower() == "user":
        values.update(user_source=0.85, relationship_relevance=0.5)
    if "permission" in event or "authorization" in event:
        values.update(authority=0.7, threat=0.3, controllability=0.35)
    if "task" in event or "run" in event:
        values.update(goal_relevance=0.6, priority=0.4, progress_delta=0.25)
    if "file" in event:
        values.update(procedural_match=0.35, actionability=0.55)
    if "operation" in event or "receipt" in event:
        values.update(actionability=0.65, causal_strength=0.45)

    numeric_hints = {
        "novelty": "novelty",
        "importance": "salience",
        "urgency": "urgency",
        "reward": "reward_delta",
        "loss": "loss_delta",
        "confidence": "predictability",
        "risk": "threat",
        "uncertainty": "uncertainty",
        "reversibility": "reversibility",
        "controllability": "controllability",
    }
    for source in (before, after, payload):
        for payload_key, dimension in numeric_hints.items():
            value = source.get(payload_key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values[dimension] = _clip(value)

    return StimulusVector.from_mapping(values)


class AdaptivePostgresNeuronRuntime(PostgresNeuronRuntime):
    """Neuron runtime that converts explicit outcomes into continuous learning."""

    def emit_event(
        self,
        *,
        workspace_id: str,
        event_type: str,
        source_table: str,
        operation: str,
        source_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stimulus = encode_database_event(
            event_type=event_type,
            operation=operation,
            payload=payload,
        )
        with self._connect() as connection:
            row = connection.execute(
                """INSERT INTO lh_stimulus_events(
                       workspace_id,event_type,source_table,source_id,operation,
                       payload,stimulus_vector,status
                   ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,'pending')
                   RETURNING *""",
                (
                    workspace_id,
                    event_type,
                    source_table,
                    source_id,
                    operation,
                    json.dumps(payload or {}, ensure_ascii=False, default=str),
                    list(stimulus.values),
                ),
            ).fetchone()
        return self._event_dict(row)

    def _stimulus_for_event(self, event: dict[str, Any]) -> StimulusVector:
        vector = event.get("stimulus_vector")
        if vector:
            return StimulusVector(tuple(float(item) for item in vector))
        return encode_database_event(
            event_type=str(event.get("event_type") or "database.change"),
            operation=str(event.get("operation") or "update"),
            payload=event.get("payload") or {},
        )

    def _process_claimed(self, event: dict[str, Any]) -> dict[str, Any]:
        result = super()._process_claimed(event)
        stimulus = StimulusVector(tuple(float(value) for value in result["stimulus_vector"]))
        reward = _clip(stimulus.get("reward_delta") - stimulus.get("loss_delta"))
        result["learning"] = {"applied": False, "reward": reward}
        if abs(reward) < 0.05:
            return result
        try:
            outcome = self.record_outcome(
                event_id=int(event["id"]),
                reward=reward,
                outcome={"automatic": True, "source": event.get("event_type")},
            )
            result["learning"] = {
                "applied": True,
                "reward": reward,
                "outcome_event_id": outcome["id"],
            }
        except Exception as exc:  # Reflex state remains valid if learning storage fails.
            result["learning"]["error"] = str(exc)
        return result
