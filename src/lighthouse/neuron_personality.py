from __future__ import annotations

from dataclasses import dataclass, replace
import math
import random
from typing import Any, Iterable, Mapping, Sequence

from .neuron_model import (
    EmergentNeuron,
    FieldResult,
    MemoryTrace,
    NeuronField,
    StimulusVector,
    _clip,
)

NEURON_COUNT = 24
STATE_VECTOR_SIZE = NEURON_COUNT * 8
IDENTITY_SCHEMA_VERSION = 1
ATTRACTOR_SIMILARITY = 0.92
CHECKPOINT_INTERVAL = 1000


def _clip01(value: float) -> float:
    return max(0.0, min(float(value), 1.0))


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    return sum(items) / len(items) if items else 0.0


def _geometric_mean(values: Sequence[float]) -> float:
    positive = [max(1e-9, abs(float(value))) for value in values]
    if not positive:
        return 0.0
    return math.exp(sum(math.log(value) for value in positive) / len(positive))


@dataclass(frozen=True, slots=True)
class CognitiveControl:
    search_depth: float
    memory_depth: float
    candidate_count: int
    verification_depth: float
    planning_depth: float
    execution_bias: float
    recovery_bias: float
    novelty_bias: float
    social_context_weight: float
    response_compression: float
    confidence: float
    dominant_circuit: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "search_depth": self.search_depth,
            "memory_depth": self.memory_depth,
            "candidate_count": self.candidate_count,
            "verification_depth": self.verification_depth,
            "planning_depth": self.planning_depth,
            "execution_bias": self.execution_bias,
            "recovery_bias": self.recovery_bias,
            "novelty_bias": self.novelty_bias,
            "social_context_weight": self.social_context_weight,
            "response_compression": self.response_compression,
            "confidence": self.confidence,
            "dominant_circuit": self.dominant_circuit,
            "source": "persistent_24_neuron_field",
            "prompt_persona": False,
        }


@dataclass(frozen=True, slots=True)
class LocalCredit:
    neuron_id: int
    global_reward: float
    local_reward: float
    contribution: float
    intrinsic_reward: float
    prediction_error: float
    responsibility: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "neuron_id": self.neuron_id,
            "global_reward": self.global_reward,
            "local_reward": self.local_reward,
            "contribution": self.contribution,
            "intrinsic_reward": self.intrinsic_reward,
            "prediction_error": self.prediction_error,
            "responsibility": self.responsibility,
        }


@dataclass(frozen=True, slots=True)
class EdgeLearning:
    source_neuron_id: int
    target_neuron_id: int
    old_weight: float
    new_weight: float
    plasticity: float
    permanence: float
    usage_count: int
    success_count: int
    failure_count: int
    dormant: bool
    coactivation: float
    target_prediction_error: float

    @property
    def edge_type(self) -> str:
        if self.new_weight > 0.005:
            return "excitatory"
        if self.new_weight < -0.005:
            return "inhibitory"
        return "adaptive"


@dataclass(frozen=True, slots=True)
class PersonalityLearningResult:
    global_reward: float
    credits: tuple[LocalCredit, ...]
    edges: tuple[EdgeLearning, ...]
    identity_signature: tuple[float, ...]


class PersistentNeuronField(NeuronField):
    """Neuron field with explicit excitation/inhibition and sparse competition."""

    def __init__(
        self,
        neurons: Sequence[EmergentNeuron] | None = None,
        *,
        relation_seed: int = 2401,
        competition_k: int = 6,
    ):
        super().__init__(neurons=neurons, relation_seed=relation_seed)
        self.competition_k = max(2, min(int(competition_k), NEURON_COUNT))

    def run(
        self,
        stimulus: StimulusVector,
        *,
        memories: Mapping[int, Iterable[MemoryTrace]] | None = None,
        max_rounds: int = 16,
        epsilon: float = 1e-4,
    ) -> FieldResult:
        memories = memories or {}
        previous = [neuron.state.activation for neuron in self.neurons]
        max_delta = math.inf
        rounds = 0

        for round_index in range(max(1, max_rounds)):
            rounds = round_index + 1
            current: list[float] = []
            for target, neuron in enumerate(self.neurons):
                excitatory = 0.0
                inhibitory = 0.0
                for source in range(NEURON_COUNT):
                    if source == target:
                        continue
                    signal = self.relations[source][target] * previous[source]
                    if signal >= 0:
                        excitatory += signal
                    else:
                        inhibitory += abs(signal)
                state = neuron.step(
                    stimulus,
                    memories=memories.get(neuron.archetype.neuron_id, ()),
                    social_input=_clip(excitatory - inhibitory),
                )
                current.append(state.activation)

            winners = {
                index
                for index, _ in sorted(
                    enumerate(current), key=lambda item: abs(item[1]), reverse=True
                )[: self.competition_k]
            }
            for index, neuron in enumerate(self.neurons):
                if index in winners:
                    continue
                suppressed = _clip(current[index] * 0.65)
                neuron.state = replace(neuron.state, activation=suppressed)
                current[index] = suppressed

            max_delta = max(
                abs(current[index] - previous[index]) for index in range(NEURON_COUNT)
            )
            previous = current
            if max_delta < epsilon:
                break

        self.last_stimulus = stimulus
        ranked = sorted(
            self.neurons,
            key=lambda neuron: abs(neuron.state.activation),
            reverse=True,
        )
        dominant = tuple(
            {
                "neuron_id": neuron.archetype.neuron_id,
                "name": neuron.archetype.name,
                "role": neuron.archetype.role,
                "activation": neuron.state.activation,
                "valence": neuron.state.valence,
                "confidence": neuron.state.confidence,
                "prediction": neuron.state.prediction,
            }
            for neuron in ranked[:6]
        )
        state_vector = tuple(
            value for neuron in self.neurons for value in neuron.state.vector()
        )
        return FieldResult(
            rounds=rounds,
            converged=max_delta < epsilon,
            max_delta=max_delta,
            dominant_neurons=dominant,
            emotions=self._emotion_projection(stimulus),
            state_vector=state_vector,
        )


def apply_identity_seed(field: NeuronField, identity_seed: int) -> None:
    """Apply tiny permanent developmental differences without assigning a persona."""

    rng = random.Random(int(identity_seed))
    for neuron in field.neurons:
        neuron.long_weights = [
            _clip(weight + rng.uniform(-0.03, 0.03), -2.0, 2.0)
            for weight in neuron.long_weights
        ]
        neuron.threshold = _clip(
            neuron.threshold + rng.uniform(-0.02, 0.02), -0.5, 1.5
        )


_ROLE_INTRINSIC: dict[str, tuple[tuple[str, float], ...]] = {
    "vigilance": (("threat", 0.45), ("anomaly", 0.35), ("failure_probability", 0.2)),
    "exploration": (("novelty", 0.4), ("information_gain", 0.4), ("opportunity", 0.2)),
    "memory": (("recall_success", 0.4), ("memory_strength_delta", 0.35), ("association_growth", 0.25)),
    "skepticism": (("contradiction", 0.45), ("uncertainty", 0.3), ("ambiguity", 0.25)),
    "empathy": (("social_warmth", 0.4), ("relationship_relevance", 0.35), ("emotional_intensity", 0.25)),
    "planning": (("dependency_change", 0.35), ("priority", 0.3), ("long_horizon", 0.35)),
    "action": (("actionability", 0.45), ("urgency", 0.25), ("progress_delta", 0.3)),
    "creation": (("novelty", 0.4), ("opportunity", 0.4), ("complexity", 0.2)),
    "stability": (("predictability", 0.4), ("reversibility", 0.35), ("coherence", 0.25)),
    "optimization": (("progress_delta", 0.4), ("coherence", 0.3), ("resource_cost", 0.3)),
    "mediation": (("cooperation", 0.4), ("conflict", 0.3), ("coherence", 0.3)),
    "challenge": (("contradiction", 0.45), ("conflict", 0.3), ("novelty", 0.25)),
    "strategy": (("long_horizon", 0.4), ("goal_relevance", 0.35), ("opportunity", 0.25)),
    "implementation": (("actionability", 0.4), ("controllability", 0.35), ("procedural_match", 0.25)),
    "audit": (("causal_strength", 0.4), ("coherence", 0.3), ("contradiction", 0.3)),
    "protection": (("reversibility", 0.35), ("threat", 0.35), ("recoverability", 0.3)),
    "explanation": (("semantic_density", 0.4), ("coherence", 0.4), ("complexity", 0.2)),
    "prediction": (("trend", 0.4), ("predictability", 0.35), ("acceleration", 0.25)),
    "goal-alignment": (("goal_relevance", 0.45), ("completion_signal", 0.3), ("progress_delta", 0.25)),
    "social": (("relationship_relevance", 0.4), ("trust_delta", 0.35), ("cooperation", 0.25)),
    "pattern": (("repetition", 0.35), ("recurrence", 0.35), ("procedural_match", 0.3)),
    "recovery": (("recoverability", 0.5), ("failure_probability", 0.25), ("controllability", 0.25)),
    "integration": (("coherence", 0.4), ("association_growth", 0.35), ("causal_strength", 0.25)),
    "metacognition": (("predictability", 0.35), ("coherence", 0.3), ("uncertainty", 0.2), ("conflict_level", 0.15)),
}


def _intrinsic_reward(neuron: EmergentNeuron, stimulus: StimulusVector) -> float:
    weighted = _ROLE_INTRINSIC.get(neuron.archetype.role, ())
    signal = sum(stimulus.get(name) * weight for name, weight in weighted)
    if neuron.archetype.role == "metacognition":
        signal -= 0.25 * abs(neuron.state.prediction - neuron.state.valence)
    return _clip(signal * (0.5 + 0.5 * abs(neuron.state.activation)))


def _numeric_outcome_signal(outcome: Mapping[str, Any] | None) -> float | None:
    if not outcome:
        return None
    values = [
        _clip(float(value))
        for value in outcome.values()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    return _mean(values) if values else None


def compute_local_credits(
    field: NeuronField,
    stimulus: StimulusVector,
    *,
    global_reward: float,
    outcome: Mapping[str, Any] | None = None,
) -> tuple[LocalCredit, ...]:
    global_reward = _clip(global_reward)
    outcome_signal = _numeric_outcome_signal(outcome)
    if outcome_signal is not None:
        global_reward = _clip(0.75 * global_reward + 0.25 * outcome_signal)

    total_activation = sum(abs(neuron.state.activation) for neuron in field.neurons) or 1.0
    credits: list[LocalCredit] = []
    for neuron in field.neurons:
        activation = abs(neuron.state.activation)
        share = activation / total_activation
        biases = neuron.archetype.persona_bias
        denominator = sum(abs(float(value)) for value in biases.values()) or 1.0
        alignment = _clip(
            sum(float(weight) * stimulus.get(name) for name, weight in biases.items())
            / denominator
        )
        contribution = _clip01(
            0.5 * activation
            + 0.25 * max(0.0, alignment)
            + 0.25 * min(1.0, share * NEURON_COUNT)
        )
        responsibility = _clip01(0.3 + 0.7 * contribution)
        intrinsic = _intrinsic_reward(neuron, stimulus)
        prediction_error = _clip(global_reward - neuron.state.prediction)
        local_reward = _clip(
            global_reward * (0.2 + 0.8 * responsibility)
            + 0.15 * intrinsic
            + 0.05 * prediction_error
        )
        credits.append(
            LocalCredit(
                neuron_id=neuron.archetype.neuron_id,
                global_reward=global_reward,
                local_reward=local_reward,
                contribution=contribution,
                intrinsic_reward=intrinsic,
                prediction_error=prediction_error,
                responsibility=responsibility,
            )
        )
    return tuple(credits)


def _normalize_outgoing(matrix: list[list[float]], *, budget: float = 2.75) -> None:
    for source in range(NEURON_COUNT):
        total = sum(abs(value) for value in matrix[source])
        if total <= budget or total == 0:
            continue
        scale = budget / total
        matrix[source] = [value * scale for value in matrix[source]]


def apply_persistent_learning(
    field: NeuronField,
    stimulus: StimulusVector,
    *,
    global_reward: float,
    outcome: Mapping[str, Any] | None = None,
    edge_metadata: Mapping[tuple[int, int], Mapping[str, Any]] | None = None,
) -> PersonalityLearningResult:
    edge_metadata = edge_metadata or {}
    field.last_stimulus = stimulus
    credits = compute_local_credits(
        field, stimulus, global_reward=global_reward, outcome=outcome
    )
    credit_by_id = {credit.neuron_id: credit for credit in credits}
    activations = [neuron.state.activation for neuron in field.neurons]

    for neuron in field.neurons:
        neuron.learn(
            stimulus,
            reward=credit_by_id[neuron.archetype.neuron_id].local_reward,
        )

    proposed = [list(row) for row in field.relations]
    provisional: dict[tuple[int, int], dict[str, Any]] = {}
    for source in range(NEURON_COUNT):
        for target in range(NEURON_COUNT):
            if source == target:
                continue
            key = (source + 1, target + 1)
            metadata = dict(edge_metadata.get(key) or {})
            old_weight = float(field.relations[source][target])
            plasticity = _clip01(float(metadata.get("plasticity", 0.01)))
            permanence = _clip01(float(metadata.get("permanence", 0.0)))
            usage_count = int(metadata.get("usage_count", 0))
            success_count = int(metadata.get("success_count", 0))
            failure_count = int(metadata.get("failure_count", 0))
            coactivation = activations[source] * activations[target]
            target_credit = credits[target]
            learning_scale = max(0.1, plasticity / 0.01)
            reinforced = (
                0.006
                * target_credit.prediction_error
                * coactivation
                * learning_scale
            )
            competitive = 0.0
            if (
                target_credit.local_reward < -0.15
                and abs(activations[source]) > 0.35
                and abs(activations[target]) > 0.35
            ):
                competitive = -0.0025 * abs(coactivation)
            decay = 0.0004 * old_weight * (1.0 - permanence)
            proposed[source][target] = _clip(
                old_weight + reinforced + competitive - decay,
                -0.75,
                0.75,
            )
            used = abs(coactivation) > 0.05
            if used:
                usage_count += 1
                if target_credit.local_reward > 0.1:
                    success_count += 1
                    permanence = _clip01(permanence + 0.002 * abs(coactivation))
                elif target_credit.local_reward < -0.1:
                    failure_count += 1
                    permanence = _clip01(permanence - 0.001 * abs(coactivation))
            provisional[key] = {
                "old_weight": old_weight,
                "plasticity": plasticity,
                "permanence": permanence,
                "usage_count": usage_count,
                "success_count": success_count,
                "failure_count": failure_count,
                "coactivation": coactivation,
                "target_prediction_error": target_credit.prediction_error,
            }

    _normalize_outgoing(proposed)
    field.relations = proposed
    edges: list[EdgeLearning] = []
    for source in range(NEURON_COUNT):
        for target in range(NEURON_COUNT):
            if source == target:
                continue
            key = (source + 1, target + 1)
            data = provisional[key]
            new_weight = float(proposed[source][target])
            dormant = bool(
                data["usage_count"] >= 64 and abs(new_weight) < 0.005
            )
            edges.append(
                EdgeLearning(
                    source_neuron_id=source + 1,
                    target_neuron_id=target + 1,
                    old_weight=data["old_weight"],
                    new_weight=new_weight,
                    plasticity=data["plasticity"],
                    permanence=data["permanence"],
                    usage_count=data["usage_count"],
                    success_count=data["success_count"],
                    failure_count=data["failure_count"],
                    dormant=dormant,
                    coactivation=data["coactivation"],
                    target_prediction_error=data["target_prediction_error"],
                )
            )

    return PersonalityLearningResult(
        global_reward=_clip(global_reward),
        credits=credits,
        edges=tuple(edges),
        identity_signature=identity_signature(field),
    )


def identity_signature(field: NeuronField) -> tuple[float, ...]:
    neuron_signature: list[float] = []
    for neuron in field.neurons:
        neuron_signature.extend(
            (
                _clip(neuron.threshold, -1.0, 1.0),
                _clip(_mean(neuron.long_weights), -1.0, 1.0),
                _clip(math.tanh(neuron.experience_count / 500.0)),
            )
        )
    edge_signature = [
        _clip(field.relations[source][target])
        for source in range(NEURON_COUNT)
        for target in range(NEURON_COUNT)
        if source != target
    ]
    return tuple(neuron_signature + edge_signature)


def derive_cognitive_control(
    field: NeuronField,
    *,
    dominant_circuit: str | None = None,
) -> CognitiveControl:
    activation = {
        neuron.archetype.role: max(0.0, neuron.state.activation)
        for neuron in field.neurons
    }

    def score(*roles: str) -> float:
        return _clip01(_mean(activation.get(role, 0.0) for role in roles))

    exploration = score("exploration", "creation", "pattern", "integration")
    caution = score("vigilance", "skepticism", "audit", "protection", "stability")
    action = score("action", "implementation", "optimization")
    planning = score("planning", "strategy", "goal-alignment", "prediction")
    social = score("empathy", "mediation", "social")
    recovery = score("recovery", "protection")
    memory = score("memory", "pattern", "integration")
    metacognition = score("metacognition")
    confidence = _clip01(
        _mean(_clip01((neuron.state.confidence + 1.0) / 2.0) for neuron in field.neurons)
    )

    search_depth = _clip01(0.35 + 0.45 * exploration + 0.2 * metacognition)
    memory_depth = _clip01(0.35 + 0.4 * memory + 0.25 * planning)
    verification_depth = _clip01(0.25 + 0.65 * caution + 0.1 * metacognition)
    planning_depth = _clip01(0.25 + 0.65 * planning + 0.1 * metacognition)
    execution_bias = _clip01(0.45 + 0.5 * action - 0.25 * caution)
    recovery_bias = _clip01(0.3 + 0.55 * recovery + 0.15 * action)
    novelty_bias = _clip01(0.25 + 0.7 * exploration - 0.15 * caution)
    social_context_weight = _clip01(0.25 + 0.7 * social)
    response_compression = _clip01(
        0.5 + 0.35 * activation.get("optimization", 0.0)
        - 0.2 * activation.get("explanation", 0.0)
    )
    candidate_count = max(3, min(16, round(3 + 13 * search_depth)))

    return CognitiveControl(
        search_depth=search_depth,
        memory_depth=memory_depth,
        candidate_count=candidate_count,
        verification_depth=verification_depth,
        planning_depth=planning_depth,
        execution_bias=execution_bias,
        recovery_bias=recovery_bias,
        novelty_bias=novelty_bias,
        social_context_weight=social_context_weight,
        response_compression=response_compression,
        confidence=confidence,
        dominant_circuit=dominant_circuit,
    )


def _canonical_cycle(nodes: Sequence[int]) -> tuple[int, ...]:
    values = tuple(nodes)
    rotations = [values[index:] + values[:index] for index in range(len(values))]
    return min(rotations)


def detect_circuits(
    field: NeuronField,
    *,
    threshold: float = 0.12,
    max_length: int = 4,
    limit: int = 8,
) -> list[dict[str, Any]]:
    adjacency: dict[int, list[int]] = {}
    for source in range(NEURON_COUNT):
        ranked = sorted(
            (
                (target, field.relations[source][target])
                for target in range(NEURON_COUNT)
                if target != source and abs(field.relations[source][target]) >= threshold
            ),
            key=lambda item: abs(item[1]),
            reverse=True,
        )[:4]
        adjacency[source] = [target for target, _ in ranked]

    found: dict[tuple[int, ...], dict[str, Any]] = {}

    def walk(start: int, current: int, path: list[int]) -> None:
        if len(path) > max_length:
            return
        for target in adjacency.get(current, []):
            if target == start and len(path) >= 2:
                cycle = _canonical_cycle(tuple(node + 1 for node in path))
                weights = [
                    field.relations[path[index]][path[(index + 1) % len(path)]]
                    for index in range(len(path))
                ]
                activation_support = _mean(
                    abs(field.neurons[node].state.activation) for node in path
                )
                strength = _clip01(
                    _geometric_mean(weights) * (0.5 + 0.5 * activation_support)
                )
                negative_edges = sum(1 for weight in weights if weight < 0)
                found[cycle] = {
                    "signature": "-".join(str(node) for node in cycle),
                    "neuron_ids": list(cycle),
                    "edge_weights": weights,
                    "circuit_strength": strength,
                    "kind": "inhibitory_balance" if negative_edges % 2 else "recurrent",
                }
                continue
            if target in path:
                continue
            walk(start, target, [*path, target])

    for start in range(NEURON_COUNT):
        walk(start, start, [start])
    return sorted(
        found.values(), key=lambda item: item["circuit_strength"], reverse=True
    )[: max(1, int(limit))]
