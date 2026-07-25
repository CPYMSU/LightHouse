# LightHouse Memory Fabric 0.8

## Purpose

Memory Fabric preserves the world state that the main AI needs across runs,
terminal restarts and conversations. It does not replace files, PostgreSQL,
Operations or Receipts. Those remain the sources of truth.

The 0.8 contract is:

```text
Raw events
  -> hidden Memory Steward
  -> facts, complete turns, summaries, entities and relations
  -> cached Context Snapshot
  -> main AI judgment
  -> direct capability or Agent Bus
  -> thin Operation Kernel
  -> Receipt
  -> world-state projection
```

Context guides intelligence. It does not silently choose a file, rewrite a model
argument or replace the main AI's semantic judgment.

## Persistent memory

Core durable tables include:

- `lh_conversations` — one conversation per Workspace and actor;
- `lh_messages` — complete user, assistant and system messages;
- `lh_memory_tasks` — active and completed goals;
- `lh_locators` — canonical file, directory, URL, application and data locators;
- `lh_files` — bounded searchable file metadata and text;
- `lh_file_revisions` — hashes tied to successful Operations;
- `lh_conversation_summaries` — older-context distillation;
- `lh_context_snapshots` — precompiled main-AI decision bundles;
- `lh_world_entities`, `lh_world_facts` and `lh_world_relations` — observed world state;
- `lh_world_inferences` and `lh_world_uncertainties` — semantic conclusions kept separate from facts;
- `lh_memory_projections` — idempotent event projection ledger.

## Complete recent turns

Every main-AI decision receives the latest eight complete conversation turns in
original form. A turn begins with a user message and includes the assistant and
system messages that followed it.

Older messages are not discarded. Memory Steward distills them in the background
into a summary, entities, relations, inferences and unresolved questions. Relevant
files and locators are independently retrieved from the current request.

The main AI therefore receives:

```text
current request
+ latest 8 complete turns
+ older distilled summary
+ active task
+ candidate entities
+ verified facts
+ inferences and uncertainties
+ recent Receipts
+ available Agents and Work Orders
```

## Distillation levels

Context can improve without blocking the foreground:

- Level 0 — raw durable messages and events;
- Level 1 — deterministic turns, task state, Receipt and file projections;
- Level 2 — model-assisted summary, entity relations, inferences and uncertainties;
- later levels may add better specialist models, cross-source reconciliation and
  long-horizon task compression without changing the foreground protocol.

Every snapshot records its `source_cursor`, `distillation_level`, creation time
and evidence sources. A lower-level snapshot remains usable while a higher-level
background job is pending.

## Context Snapshot cache

`ContextCompiler` hashes the current request and the latest message, task, file,
summary and Work Order cursors. If none changed, the main AI reuses the existing
`lh_context_snapshots` payload instead of rebuilding memory on every reasoning
step.

A new message, task update, file projection, specialist result or upgraded
summary changes the source cursor and naturally produces a new snapshot.

## Background Memory Steward

Workspace scanning, file indexing and conversation distillation run through
`lh_background_jobs`. Jobs use PostgreSQL leases and `FOR UPDATE SKIP LOCKED`.
Repeated work for the same subject is coalesced, so rapid changes to one file
produce one latest-state indexing job rather than a queue of obsolete versions.

Memory Steward is hidden, low priority and read/index oriented. It never modifies
user files, controls the Desktop or writes business data. While a foreground main
AI run is actively reasoning, optional memory jobs yield model and I/O capacity.
Waiting for user input or a confirmation card does not stop background upkeep.

`/reindex` schedules a background Workspace scan and returns immediately.

## Address contract

The main AI owns semantic target selection. It may use recent turns, active tasks,
file candidates, Reality Agents or direct inspection to decide which file or
directory the user means.

The final address layer does not perform semantic substitution. It only validates:

- the chosen path is real when the capability requires an existing object;
- the path type matches the operation;
- the path remains inside the bound `allowed_roots`;
- relative paths do not traverse through `..`;
- symbolic-link and parent-directory constraints are satisfied.

A real directory inside the Workspace is not rejected merely because it has not
yet been indexed. An invented path is rejected and returned to the main AI as
reality feedback; it is never silently replaced with the previous active file.

## Receipts and freshness

Files and policy observations may become stale, so Reality Agents attach observed
time, evidence and volatility. The Operation Kernel repeats the minimum atomic
checks at execution time. Receipts and committed transaction outcomes are durable
facts and do not expire.

## Conversation controls

The terminal reuses the current conversation automatically. `/new` starts a new
conversation while retaining long-term indexed memory. No keyword list determines
whether a request is a continuation. The main AI judges that relationship from
complete recent turns and distilled task context.
