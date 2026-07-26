from __future__ import annotations

from lighthouse.neuron_model import NeuronState, StimulusVector
from lighthouse.neuron_personality import (
    PersistentNeuronField,
    apply_identity_seed,
    apply_persistent_learning,
    derive_cognitive_control,
    detect_circuits,
    identity_signature,
)


def test_identity_seed_is_stable_but_creates_individual_developmental_variation():
    first = PersistentNeuronField(relation_seed=101)
    second = PersistentNeuronField(relation_seed=101)
    third = PersistentNeuronField(relation_seed=202)
    apply_identity_seed(first, 9001)
    apply_identity_seed(second, 9001)
    apply_identity_seed(third, 9002)

    assert identity_signature(first) == identity_signature(second)
    assert identity_signature(first) != identity_signature(third)


def test_opposite_experience_produces_different_persistent_networks():
    stimulus = StimulusVector.from_mapping(
        {"novelty": 0.9, "information_gain": 0.8, "opportunity": 0.7}
    )
    rewarded = PersistentNeuronField(relation_seed=321)
    punished = PersistentNeuronField(relation_seed=321)
    apply_identity_seed(rewarded, 777)
    apply_identity_seed(punished, 777)

    for _ in range(48):
        rewarded.run(stimulus, max_rounds=4)
        punished.run(stimulus, max_rounds=4)
        apply_persistent_learning(rewarded, stimulus, global_reward=0.9)
        apply_persistent_learning(punished, stimulus, global_reward=-0.7)

    assert identity_signature(rewarded) != identity_signature(punished)
    max_difference = max(
        abs(rewarded.relations[source][target] - punished.relations[source][target])
        for source in range(24)
        for target in range(24)
        if source != target
    )
    assert max_difference > 0.001
    assert all(
        sum(abs(value) for value in rewarded.relations[source]) <= 2.750001
        for source in range(24)
    )


def test_local_credit_assignment_does_not_reward_every_neuron_equally():
    stimulus = StimulusVector.from_mapping(
        {"threat": 0.9, "failure_probability": 0.8, "recoverability": 0.6}
    )
    field = PersistentNeuronField(relation_seed=404)
    field.run(stimulus, max_rounds=5)
    learned = apply_persistent_learning(field, stimulus, global_reward=-0.8)

    local_rewards = {round(item.local_reward, 6) for item in learned.credits}
    contributions = {round(item.contribution, 6) for item in learned.credits}
    assert len(local_rewards) > 4
    assert len(contributions) > 4


def test_cognitive_control_is_derived_from_live_activation_not_persona_text():
    field = PersistentNeuronField(relation_seed=505)
    for neuron in field.neurons:
        neuron.state = NeuronState()
    for neuron_id in (2, 8, 21, 23):
        field.neurons[neuron_id - 1].state = NeuronState(
            activation=0.9,
            confidence=0.7,
        )

    control = derive_cognitive_control(field)
    assert control.search_depth > 0.65
    assert control.novelty_bias > 0.65
    assert control.candidate_count >= 11
    assert control.as_dict()["prompt_persona"] is False


def test_recurrent_circuit_is_detected_from_database_edge_shape():
    field = PersistentNeuronField(relation_seed=606)
    field.relations[1][7] = 0.55
    field.relations[7][22] = 0.50
    field.relations[22][1] = 0.45
    for neuron_id in (2, 8, 23):
        field.neurons[neuron_id - 1].state = NeuronState(activation=0.8)

    circuits = detect_circuits(field, threshold=0.2, limit=5)
    assert circuits
    assert circuits[0]["signature"] == "2-8-23"
    assert circuits[0]["kind"] == "recurrent"
