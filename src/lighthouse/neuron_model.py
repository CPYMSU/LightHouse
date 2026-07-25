from __future__ import annotations

from dataclasses import dataclass, field
import math
import random
from typing import Any, Iterable, Mapping, Sequence


STIMULUS_DIMENSIONS: tuple[str, ...] = (
    # Basic perception
    "intensity",
    "novelty",
    "familiarity",
    "persistence",
    "repetition",
    "anomaly",
    "salience",
    "sensory_conflict",
    # Value and affect
    "positive_valence",
    "negative_valence",
    "reward_delta",
    "loss_delta",
    "trust_delta",
    "threat",
    "social_warmth",
    "aversion",
    # Cognition
    "uncertainty",
    "complexity",
    "causal_strength",
    "contradiction",
    "information_gain",
    "predictability",
    "ambiguity",
    "coherence",
    # Action
    "urgency",
    "controllability",
    "reversibility",
    "actionability",
    "resource_cost",
    "failure_probability",
    "opportunity",
    "recoverability",
    # Goal
    "goal_relevance",
    "progress_delta",
    "priority",
    "dependency_change",
    "completion_signal",
    "blocked_signal",
    "long_horizon",
    "short_horizon",
    # Social
    "user_source",
    "authority",
    "emotional_intensity",
    "relationship_relevance",
    "expertise",
    "cooperation",
    "conflict",
    "attachment",
    # Time
    "recency",
    "duration",
    "acceleration",
    "trend",
    "periodicity",
    "deadline_pressure",
    "staleness",
    "recurrence",
    # Memory
    "memory_strength_delta",
    "retrieval_frequency",
    "conflict_level",
    "association_growth",
    "forgetting_pressure",
    "recall_success",
    "procedural_match",
    "semantic_density",
)
DIMENSION_INDEX = {name: index for index, name in enumerate(STIMULUS_DIMENSIONS)}
VECTOR_SIZE = len(STIMULUS_DIMENSIONS)


def _clip(value: float, lower: float = -1.0, upper: float = 1.0) -> float:
    return max(lower, min(float(value), upper))


def _dot(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have the same length")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return _clip(_dot(left, right) / (left_norm * right_norm))


@dataclass(frozen=True, slots=True)
class StimulusVector:
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.values) != VECTOR_SIZE:
            raise ValueError(f"stimulus vector must contain {VECTOR_SIZE} values")
        object.__setattr__(self, "values", tuple(_clip(value) for value in self.values))

    @classmethod
    def zero(cls) -> "StimulusVector":
        return cls((0.0,) * VECTOR_SIZE)

    @classmethod
    def from_mapping(cls, values: Mapping[str, float]) -> "StimulusVector":
        vector = [0.0] * VECTOR_SIZE
        for name, value in values.items():
            try:
                vector[DIMENSION_INDEX[name]] = _clip(value)
            except KeyError as exc:
                raise KeyError(f"unknown stimulus dimension: {name}") from exc
        return cls(tuple(vector))

    def get(self, name: str) -> float:
        return self.values[DIMENSION_INDEX[name]]

    def as_dict(self, *, include_zero: bool = False) -> dict[str, float]:
        return {
            name: self.values[index]
            for index, name in enumerate(STIMULUS_DIMENSIONS)
            if include_zero or self.values[index] != 0
        }


@dataclass(frozen=True, slots=True)
class MemoryTrace:
    vector: StimulusVector
    strength: float = 1.0
    valence: float = 0.0
    reward: float = 0.0

    def score(self, stimulus: StimulusVector) -> float:
        similarity = max(0.0, cosine_similarity(self.vector.values, stimulus.values))
        value_gain = 1.0 + 0.25 * _clip(self.reward) + 0.15 * _clip(self.valence)
        return similarity * max(0.0, self.strength) * max(0.1, value_gain)


@dataclass(frozen=True, slots=True)
class NeuronArchetype:
    neuron_id: int
    name: str
    role: str
    seed: int
    persona_bias: Mapping[str, float]
    learning_rate: float = 0.025
    exploration_rate: float = 0.01


def _archetype(
    neuron_id: int,
    name: str,
    role: str,
    seed: int,
    **persona_bias: float,
) -> NeuronArchetype:
    return NeuronArchetype(
        neuron_id=neuron_id,
        name=name,
        role=role,
        seed=seed,
        persona_bias=persona_bias,
    )


DEFAULT_ARCHETYPES: tuple[NeuronArchetype, ...] = (
    _archetype(1, "Sentinel", "vigilance", 1101, threat=0.8, anomaly=0.65, failure_probability=0.55),
    _archetype(2, "Explorer", "exploration", 1102, novelty=0.8, information_gain=0.7, opportunity=0.45),
    _archetype(3, "Archivist", "memory", 1103, memory_strength_delta=0.75, recall_success=0.65, association_growth=0.6),
    _archetype(4, "Skeptic", "skepticism", 1104, contradiction=0.8, uncertainty=0.55, ambiguity=0.5),
    _archetype(5, "Empath", "empathy", 1105, emotional_intensity=0.7, social_warmth=0.65, relationship_relevance=0.6),
    _archetype(6, "Planner", "planning", 1106, dependency_change=0.7, priority=0.55, long_horizon=0.6),
    _archetype(7, "Actor", "action", 1107, actionability=0.8, urgency=0.55, short_horizon=0.45),
    _archetype(8, "Creator", "creation", 1108, novelty=0.6, opportunity=0.7, complexity=0.35),
    _archetype(9, "Conservator", "stability", 1109, predictability=0.7, reversibility=0.6, anomaly=-0.35),
    _archetype(10, "Optimizer", "optimization", 1110, resource_cost=0.65, progress_delta=0.55, coherence=0.45),
    _archetype(11, "Mediator", "mediation", 1111, cooperation=0.7, conflict=0.55, coherence=0.55),
    _archetype(12, "Challenger", "challenge", 1112, contradiction=0.65, conflict=0.55, novelty=0.35),
    _archetype(13, "Strategist", "strategy", 1113, long_horizon=0.8, goal_relevance=0.65, opportunity=0.5),
    _archetype(14, "Craftsperson", "implementation", 1114, actionability=0.7, controllability=0.6, procedural_match=0.6),
    _archetype(15, "Auditor", "audit", 1115, causal_strength=0.7, coherence=0.65, contradiction=0.55),
    _archetype(16, "Guardian", "protection", 1116, reversibility=0.75, threat=0.6, loss_delta=0.55),
    _archetype(17, "Interpreter", "explanation", 1117, semantic_density=0.7, coherence=0.65, complexity=0.4),
    _archetype(18, "Forecaster", "prediction", 1118, trend=0.7, predictability=0.6, acceleration=0.55),
    _archetype(19, "Goalkeeper", "goal-alignment", 1119, goal_relevance=0.8, completion_signal=0.65, blocked_signal=0.55),
    _archetype(20, "Social Observer", "social", 1120, relationship_relevance=0.7, authority=0.55, trust_delta=0.5),
    _archetype(21, "Pattern Hunter", "pattern", 1121, repetition=0.7, recurrence=0.65, procedural_match=0.55),
    _archetype(22, "Restorer", "recovery", 1122, recoverability=0.8, failure_probability=0.55, controllability=0.45),
    _archetype(23, "Integrator", "integration", 1123, coherence=0.7, association_growth=0.6, causal_strength=0.5),
    _archetype(24, "Metacognitive Observer", "metacognition", 1124, uncertainty=0.55, conflict_level=0.6, predictability=0.5),
)


@dataclass(slots=True)
class NeuronState:
    activation: float = 0.0
    valence: float = 0.0
    arousal: float = 0.0
    confidence: float = 0.0
    fatigue: float = 0.0
    curiosity: float = 0.0
    taste: float = 0.0
    prediction: float = 0.0
    version: int = 0

    def vector(self) -> tuple[float, ...]:
        return (
            self.activation,
            self.valence,
            self.arousal,
            self.confidence,
            self.fatigue,
            self.curiosity,
            self.taste,
            self.prediction,
        )


@dataclass(slots=True)
class EmergentNeuron:
    archetype: NeuronArchetype
    long_weights: list[float] = field(default_factory=list)
    short_weights: list[float] = field(default_factory=list)
    eligibility_trace: list[float] = field(default_factory=list)
    threshold: float = 0.15
    state: NeuronState = field(default_factory=NeuronState)
    experience_count: int = 0

    def __post_init__(self) -> None:
        if not self.long_weights:
            rng = random.Random(self.archetype.seed)
            self.long_weights = [rng.gauss(0.0, 0.08) for _ in range(VECTOR_SIZE)]
            for name, bias in self.archetype.persona_bias.items():
                self.long_weights[DIMENSION_INDEX[name]] += 0.35 * float(bias)
        if not self.short_weights:
            self.short_weights = [0.0] * VECTOR_SIZE
        if not self.eligibility_trace:
            self.eligibility_trace = [0.0] * VECTOR_SIZE
        for vector in (self.long_weights, self.short_weights, self.eligibility_trace):
            if len(vector) != VECTOR_SIZE:
                raise ValueError(f"neuron weight vectors must contain {VECTOR_SIZE} values")

    @property
    def effective_weights(self) -> tuple[float, ...]:
        return tuple(
            _clip(long_value + 0.65 * short_value, -2.0, 2.0)
            for long_value, short_value in zip(
                self.long_weights, self.short_weights, strict=True
            )
        )

    def memory_resonance(
        self,
        stimulus: StimulusVector,
        traces: Iterable[MemoryTrace] = (),
        *,
        top_k: int = 8,
    ) -> float:
        ranked = sorted(
            (trace.score(stimulus) for trace in traces),
            reverse=True,
        )[: max(1, top_k)]
        if not ranked:
            return 0.0
        return _clip(sum(ranked) / max(1.0, math.sqrt(len(ranked))))

    def step(
        self,
        stimulus: StimulusVector,
        *,
        memories: Iterable[MemoryTrace] = (),
        social_input: float = 0.0,
    ) -> NeuronState:
        resonance = self.memory_resonance(stimulus, memories)
        weighted = _dot(self.effective_weights, stimulus.values) / math.sqrt(VECTOR_SIZE)
        persona_drive = sum(self.archetype.persona_bias.values()) / max(
            1.0, len(self.archetype.persona_bias)
        )
        pre_activation = (
            weighted
            + 0.35 * resonance
            + _clip(social_input, -1.0, 1.0)
            + 0.08 * persona_drive
            - self.threshold
            - 0.2 * self.state.fatigue
        )
        exploration = random.Random(
            self.archetype.seed + self.state.version
        ).uniform(-1.0, 1.0)
        activation = math.tanh(
            pre_activation + self.archetype.exploration_rate * exploration
        )

        value_signal = (
            stimulus.get("positive_valence")
            + stimulus.get("reward_delta")
            + stimulus.get("trust_delta")
            + stimulus.get("social_warmth")
            - stimulus.get("negative_valence")
            - stimulus.get("loss_delta")
            - stimulus.get("threat")
            - stimulus.get("aversion")
        ) / 4.0
        confidence_signal = (
            stimulus.get("predictability")
            + stimulus.get("coherence")
            + stimulus.get("causal_strength")
            - stimulus.get("uncertainty")
            - stimulus.get("ambiguity")
            - stimulus.get("contradiction")
        ) / 3.0
        curiosity_signal = (
            stimulus.get("novelty")
            + stimulus.get("information_gain")
            + stimulus.get("opportunity")
            - stimulus.get("familiarity")
            - stimulus.get("repetition")
        ) / 3.0
        taste_signal = (
            stimulus.get("positive_valence")
            + stimulus.get("reward_delta")
            + stimulus.get("procedural_match")
            - stimulus.get("negative_valence")
            - stimulus.get("loss_delta")
            - stimulus.get("aversion")
        ) / 3.0

        previous = self.state
        fatigue = _clip(
            previous.fatigue * 0.9
            + max(0.0, abs(activation) - 0.55) * 0.16
            + max(0.0, stimulus.get("repetition")) * 0.03,
            0.0,
            1.0,
        )
        prediction = math.tanh(weighted + 0.25 * resonance)
        self.state = NeuronState(
            activation=_clip(0.35 * previous.activation + 0.65 * activation),
            valence=_clip(0.7 * previous.valence + 0.3 * value_signal),
            arousal=_clip(0.7 * previous.arousal + 0.3 * abs(activation), 0.0, 1.0),
            confidence=_clip(0.7 * previous.confidence + 0.3 * confidence_signal),
            fatigue=fatigue,
            curiosity=_clip(0.7 * previous.curiosity + 0.3 * curiosity_signal),
            taste=_clip(0.75 * previous.taste + 0.25 * taste_signal),
            prediction=_clip(prediction),
            version=previous.version + 1,
        )
        return self.state

    def learn(self, stimulus: StimulusVector, *, reward: float) -> float:
        reward = _clip(reward)
        prediction_error = reward - self.state.prediction
        gamma_lambda = 0.82
        learning_rate = self.archetype.learning_rate
        repetition = max(0.0, stimulus.get("repetition"))
        neutral_repetition = repetition * (1.0 - abs(reward))

        for index, value in enumerate(stimulus.values):
            self.eligibility_trace[index] = _clip(
                gamma_lambda * self.eligibility_trace[index] + value
            )
            hebbian = 0.004 * self.state.activation * value
            reinforced = learning_rate * prediction_error * self.eligibility_trace[index]
            habituation = 0.003 * neutral_repetition * value
            sparse_decay = 0.0006 * math.copysign(
                1.0, self.short_weights[index]
            ) if self.short_weights[index] else 0.0
            self.short_weights[index] = _clip(
                self.short_weights[index]
                + reinforced
                + hebbian
                - habituation
                - sparse_decay,
                -1.5,
                1.5,
            )

        target_activation = 0.24
        self.threshold = _clip(
            self.threshold + 0.01 * (abs(self.state.activation) - target_activation),
            -0.5,
            1.5,
        )
        self.experience_count += 1
        if self.experience_count % 32 == 0:
            self.consolidate()
        return prediction_error

    def consolidate(self, *, rate: float = 0.08) -> None:
        rate = max(0.0, min(float(rate), 1.0))
        for index in range(VECTOR_SIZE):
            self.long_weights[index] = _clip(
                self.long_weights[index] + rate * self.short_weights[index],
                -2.0,
                2.0,
            )
            self.short_weights[index] *= 0.5


@dataclass(frozen=True, slots=True)
class FieldResult:
    rounds: int
    converged: bool
    max_delta: float
    dominant_neurons: tuple[dict[str, Any], ...]
    emotions: dict[str, float]
    state_vector: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "rounds": self.rounds,
            "converged": self.converged,
            "max_delta": self.max_delta,
            "dominant_neurons": list(self.dominant_neurons),
            "emotions": dict(self.emotions),
            "state_vector": list(self.state_vector),
        }


class NeuronField:
    """Twenty-four autonomous neurons interacting through an adaptive ABM field."""

    def __init__(
        self,
        neurons: Sequence[EmergentNeuron] | None = None,
        *,
        relation_seed: int = 2401,
    ):
        self.neurons = list(neurons or (EmergentNeuron(item) for item in DEFAULT_ARCHETYPES))
        if len(self.neurons) != 24:
            raise ValueError("the Lighthouse neuron field requires exactly 24 neurons")
        rng = random.Random(relation_seed)
        self.relations = [
            [0.0 if source == target else rng.uniform(-0.025, 0.025) for target in range(24)]
            for source in range(24)
        ]
        self.last_stimulus = StimulusVector.zero()

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
                social_input = sum(
                    self.relations[source][target] * previous[source]
                    for source in range(24)
                    if source != target
                )
                state = neuron.step(
                    stimulus,
                    memories=memories.get(neuron.archetype.neuron_id, ()),
                    social_input=social_input,
                )
                current.append(state.activation)
            max_delta = max(
                abs(current[index] - previous[index]) for index in range(24)
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
        emotions = self._emotion_projection(stimulus)
        state_vector = tuple(
            value
            for neuron in self.neurons
            for value in neuron.state.vector()
        )
        return FieldResult(
            rounds=rounds,
            converged=max_delta < epsilon,
            max_delta=max_delta,
            dominant_neurons=dominant,
            emotions=emotions,
            state_vector=state_vector,
        )

    def apply_outcome(self, *, reward: float) -> None:
        reward = _clip(reward)
        activations = [neuron.state.activation for neuron in self.neurons]
        for neuron in self.neurons:
            neuron.learn(self.last_stimulus, reward=reward)
        for source in range(24):
            for target in range(24):
                if source == target:
                    continue
                value = self.relations[source][target]
                cooperative_update = 0.004 * reward * activations[source] * activations[target]
                self.relations[source][target] = _clip(
                    value + cooperative_update - 0.0005 * value,
                    -0.5,
                    0.5,
                )

    def _emotion_projection(self, stimulus: StimulusVector) -> dict[str, float]:
        mean_valence = sum(neuron.state.valence for neuron in self.neurons) / 24.0
        mean_curiosity = sum(neuron.state.curiosity for neuron in self.neurons) / 24.0
        mean_confidence = sum(neuron.state.confidence for neuron in self.neurons) / 24.0
        mean_arousal = sum(neuron.state.arousal for neuron in self.neurons) / 24.0
        return {
            "joy": _clip(max(0.0, mean_valence + stimulus.get("reward_delta")), 0.0, 1.0),
            "fear": _clip(
                max(
                    0.0,
                    stimulus.get("threat")
                    + stimulus.get("failure_probability")
                    + stimulus.get("uncertainty"),
                )
                / 3.0,
                0.0,
                1.0,
            ),
            "curiosity": _clip(max(0.0, mean_curiosity), 0.0, 1.0),
            "trust": _clip(
                max(0.0, stimulus.get("trust_delta") + mean_confidence) / 2.0,
                0.0,
                1.0,
            ),
            "aversion": _clip(
                max(
                    0.0,
                    stimulus.get("aversion")
                    + stimulus.get("negative_valence")
                    + stimulus.get("loss_delta"),
                )
                / 3.0,
                0.0,
                1.0,
            ),
            "confusion": _clip(
                max(
                    0.0,
                    stimulus.get("uncertainty")
                    + stimulus.get("ambiguity")
                    + stimulus.get("contradiction")
                    - mean_confidence,
                )
                / 3.0,
                0.0,
                1.0,
            ),
            "arousal": _clip(mean_arousal, 0.0, 1.0),
        }


def encode_database_change(
    *,
    event_type: str,
    operation: str,
    payload: Mapping[str, Any] | None = None,
) -> StimulusVector:
    """Deterministically turn a database change into a 64-dimensional stimulus."""

    payload = payload or {}
    event = event_type.strip().lower()
    operation = operation.strip().lower()
    values: dict[str, float] = {
        "intensity": min(1.0, 0.12 + 0.03 * len(payload)),
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
    if any(token in event for token in ("success", "succeeded", "completed", "receipt.ok")):
        values.update(
            positive_valence=0.75,
            reward_delta=0.85,
            progress_delta=0.75,
            completion_signal=0.65,
            predictability=0.4,
        )
    if any(token in event for token in ("failure", "failed", "error", "receipt.failed")):
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
    for payload_key, dimension in numeric_hints.items():
        value = payload.get(payload_key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            values[dimension] = _clip(value)

    return StimulusVector.from_mapping(values)
