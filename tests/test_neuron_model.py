from __future__ import annotations

from lighthouse.neuron_adaptation import encode_database_event
from lighthouse.neuron_model import (
    DEFAULT_ARCHETYPES,
    DIMENSION_INDEX,
    MemoryTrace,
    NeuronField,
    StimulusVector,
    VECTOR_SIZE,
    encode_database_change,
)


def test_neuron_field_has_24_distinct_personalities_and_64_dimensional_stimuli():
    assert VECTOR_SIZE == 64
    assert len(DEFAULT_ARCHETYPES) == 24
    assert len({item.seed for item in DEFAULT_ARCHETYPES}) == 24
    assert len({item.role for item in DEFAULT_ARCHETYPES}) == 24

    stimulus = StimulusVector.from_mapping(
        {"threat": 0.8, "novelty": 0.5, "memory_strength_delta": 0.6}
    )
    field = NeuronField()
    result = field.run(stimulus, max_rounds=8)

    activations = {
        round(neuron.state.activation, 8) for neuron in field.neurons
    }
    assert len(activations) > 12
    assert len(result.state_vector) == 24 * 8
    assert len(result.dominant_neurons) == 6


def test_database_changes_are_encoded_without_a_model_call():
    first = encode_database_change(
        event_type="lh_operation_receipts.failed",
        operation="insert",
        payload={"risk": 0.9, "urgency": 0.8},
    )
    second = encode_database_change(
        event_type="lh_operation_receipts.failed",
        operation="insert",
        payload={"risk": 0.9, "urgency": 0.8},
    )

    assert first == second
    assert first.get("threat") == 0.9
    assert first.get("urgency") == 0.8
    assert first.get("failure_probability") > 0
    assert first.get("negative_valence") > 0


def test_each_neuron_recalls_its_own_vector_database():
    stimulus = StimulusVector.from_mapping(
        {"novelty": 0.8, "information_gain": 0.7}
    )
    matching = MemoryTrace(
        vector=stimulus,
        strength=1.4,
        valence=0.6,
        reward=0.8,
    )
    field_without_memory = NeuronField()
    field_with_memory = NeuronField()

    field_without_memory.run(stimulus, max_rounds=1)
    field_with_memory.run(
        stimulus,
        memories={2: [matching]},
        max_rounds=1,
    )

    explorer_without = field_without_memory.neurons[1].state.activation
    explorer_with = field_with_memory.neurons[1].state.activation
    assert explorer_with > explorer_without


def test_rewarded_repetition_changes_neuron_sensitivity_and_consolidates():
    stimulus = StimulusVector.from_mapping(
        {
            "threat": 0.9,
            "failure_probability": 0.8,
            "recoverability": 0.6,
        }
    )
    field = NeuronField()
    threat_index = DIMENSION_INDEX["threat"]
    before = [
        neuron.effective_weights[threat_index] for neuron in field.neurons
    ]

    for _ in range(40):
        field.run(stimulus, max_rounds=2)
        field.apply_outcome(reward=1.0)

    after = [
        neuron.effective_weights[threat_index] for neuron in field.neurons
    ]
    assert any(later > earlier for earlier, later in zip(before, after, strict=True))
    assert all(neuron.experience_count == 40 for neuron in field.neurons)


def test_failure_stimulus_projects_animal_like_fear_and_aversion():
    stimulus = encode_database_change(
        event_type="memory.failed",
        operation="insert",
        payload={"risk": 0.95, "uncertainty": 0.7},
    )
    result = NeuronField().run(stimulus, max_rounds=8)

    assert result.emotions["fear"] > 0.5
    assert result.emotions["aversion"] > 0.3
    assert result.emotions["joy"] == 0


def test_nested_receipt_result_becomes_reward_or_loss_stimulus():
    failed = encode_database_event(
        event_type="lh_operation_receipts.insert",
        operation="insert",
        payload={"after": {"ok": False}},
    )
    succeeded = encode_database_event(
        event_type="lh_operation_receipts.insert",
        operation="insert",
        payload={"after": {"ok": True}},
    )

    assert failed.get("loss_delta") > 0.5
    assert failed.get("threat") > 0.5
    assert succeeded.get("reward_delta") > 0.5
    assert succeeded.get("positive_valence") > 0.5
