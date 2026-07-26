# Cognitive State Projection 1.0

## Position

LightHouse owns a persistent world. The external model receives a projection of that world for one decision, but the projection must never become a semantic router, a fixed workflow, or a reduced authority surface.

The invariant is:

```text
LightHouse world completeness = persistent
Model serialization = compact, canonical and expandable
Main AI semantic authority = unchanged
Operation Kernel authority and Receipt truth = unchanged
```

The goal is not to make the main AI forget tools, history, data worlds, Agents or projects. The goal is to stop serializing the same world several times in several incompatible shapes.

## Previous duplication

Before this projection layer, one model call could contain overlapping copies of:

- raw Run Steps;
- Cognitive Continuity derived from those Steps;
- `context_intelligence`;
- a second `memory` copy derived from the same Context bundle;
- the complete verbose Capability Atlas in the system prompt;
- Tool Registry recommendations in model state;
- Agent Observatory, Work Orders, fused results and execution activity;
- Data World resources including repeated column lists;
- project and Massive Build records with nested payloads.

This increased Token usage without increasing cognitive coverage. It also weakened conversational salience because the latest user move competed with large repeated payloads.

## Canonical projection

`CognitiveProjectionMixin` is the first mixin in `MegaProjectLightHouseBrain`, but it calls `super()` before projecting. Therefore all existing lower mixins continue to receive and process the full runtime state. Compaction occurs only after Context Intelligence, Cognitive Continuity, engineering state, Work Intensity, Agent fusion, execution observability and neuron control have completed their work.

The provider receives one canonical state:

```text
run + workspace
  -> dialogue_focus
  -> cognitive_continuity
  -> compact complete run_ledger
  -> memory_world
  -> capability_world
  -> data_worlds
  -> project_world
  -> agent_world
  -> neuron_field + cognitive_control
  -> cognition_receipt
```

No greeting classifier, keyword branch or task-type router is introduced. A greeting and a repository-wide engineering request pass through the same projection contract and retain the same complete capability topology.

## Complete capability topology

The full verbose capability schemas are no longer repeated in the system prompt. Instead, `capability_world.complete_map` contains every callable capability for the active Kernel mode, grouped by domain, with:

- exact tool name;
- command name;
- compact argument manifest;
- Kernel;
- risk;
- confirmation mode;
- write flag.

The map is explicitly:

```text
complete = true
ranked = false
semantic_limit = null
```

Tool recommendations remain a current attention focus only. They are not the capability boundary. The main AI may inspect any schema, expand any node, traverse another domain, request the full atlas, repeat discovery, or ignore recommendations.

## Dialogue continuity

`dialogue_focus` preserves the relationship between:

- the latest user input or direction;
- the immediately preceding `input_required` move;
- complete recent turns;
- the active task;
- candidate entities.

The main AI is instructed to resolve whether the user is answering, accepting, rejecting, correcting or challenging the preceding move before asking again. This is semantic guidance, not a phrase list or deterministic interpretation.

This directly addresses cases where a user asks a follow-up such as “会影响到主 AI 吗” after discussing the neuron field, or challenges an unnecessary clarification with “你能看到上文吗”.

## Entropy reduction without world reduction

Raw operation payloads remain durable. The model projection replaces repeated large payloads with topology and evidence references:

- raw Steps become a complete compact event index;
- patches and large Receipt bodies are not repeated in the event index;
- Memory and Context Intelligence become one `memory_world`;
- the Capability Atlas and recommendations become one `capability_world`;
- Agent state becomes one `agent_world`;
- Data Worlds preserve targets, resource names, primary keys, writable columns, command topology and column-manifest hashes; full schema remains expandable;
- project records preserve identifiers, status, dependencies and findings while nested raw project state remains expandable.

This is a representation transform. It does not delete durable records or revoke access.

## Neuron field

Persistent neuron controls continue to influence attention and runtime preferences. Candidate counts and retrieval budgets are recorded as initial attention resolution, not permanent visibility boundaries. The full compact capability topology remains visible regardless of the current Tool Registry recommendation count.

Neuron state still cannot expand Workspace authority, allowed roots, confirmation rules or Receipt truth.

## Cognition Receipt

Every projected state includes a `cognition_receipt` with:

- raw and projected character counts;
- projection fingerprint;
- folded duplicate sections;
- world-coverage declaration;
- confirmation that keyword routing, fixed workflow and semantic restrictions were not added;
- confirmation that Kernel authority is unchanged.

This makes Token regressions and context-loss regressions inspectable instead of anecdotal.

## Expected effect

A trivial conversational turn should no longer carry full verbose schemas, duplicate memory structures, raw patch bodies and repeated Agent state. Complex tasks retain the same world map and can freely expand relevant nodes. The reduction comes from higher information density, not from classifying a request as “simple” and disabling the rest of LightHouse.
