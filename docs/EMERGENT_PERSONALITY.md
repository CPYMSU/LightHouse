# Persistent Emergent Personality

LightHouse does not store personality as a prompt, label or role-play profile. The persistent personality system is the long-term state of the existing twenty-four-neuron field.

## What is persistent

Each Workspace owns one durable neural identity:

- a permanent developmental seed;
- 24 neuron states and 64-dimensional stimulus sensitivities;
- 552 directed excitatory, inhibitory or adaptive edges;
- short- and long-term weights, eligibility traces and thresholds;
- local credit-assignment history;
- recurrent circuits and attractor centroids;
- periodic identity checkpoints;
- the latest programmatic `CognitiveControl` snapshot.

The background worker reloads this state for every stimulus and writes the learned state back in one PostgreSQL transaction. Creating a new conversation or restarting the LightHouse process does not reset the field.

## Learning loop

1. Database and conversation events become deterministic 64-dimensional stimuli.
2. Message triggers retain no raw content in the stimulus payload, but derive bounded interaction signals such as approval, correction, rejection, frustration, continuation and requested directness.
3. The field runs bounded recurrent rounds with separate excitation and inhibition plus sparse winner-take-more competition.
4. Each neuron receives an independent local reward based on global outcome, activation contribution, role alignment, prediction error and a bounded intrinsic drive.
5. Neuron sensitivities, thresholds and directed edges are updated and normalized.
6. Successful connections slowly gain permanence; unused weak edges can become dormant.
7. The converged state is matched to a durable attractor and recurrent circuits are detected from the learned graph.
8. A structural identity signature is updated and periodically checkpointed.

Neutral events also update eligibility, habituation and bounded intrinsic learning. Positive and negative user or execution outcomes add stronger reinforcement. This closes the previous gap where only explicit outcomes changed weights.

## Programmatic behavior control

The neural field produces numeric controls rather than a persona prompt:

- context turn and file budgets;
- memory depth;
- tool recommendation candidate count;
- planning and verification depth;
- execution, recovery and novelty biases;
- social-context weighting and response compression.

`NeuronAwareContextCompiler` applies retrieval budgets before the model call. `MegaProjectContextCompiler` applies the candidate and project-context budgets before tool recommendations are assembled. The model receives the resulting runtime state, but no instruction claiming that it has a named personality.

## PostgreSQL objects

Migration `0009_persistent_emergent_personality.sql` adds:

- `lh_neuron_identities`
- `lh_neuron_learning_events`
- `lh_neuron_edge_history`
- `lh_neuron_attractors`
- `lh_neuron_circuits`
- `lh_neuron_controls`
- `lh_neuron_checkpoints`

It also extends neuron states and edges with plasticity, permanence, stability, inhibition, usage and dormancy metadata.

## Scope and safety

Personality persistence is scoped to a Workspace. It changes attention, learning and decision budgets but does not expand Workspace authority, allowed roots, confirmation requirements or Receipt truth. The deterministic Kernel remains the authority and reality boundary.
