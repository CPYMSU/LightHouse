# Memory Resolution / 分級記憶解析

## Purpose

Memory Fabric keeps the durable record in PostgreSQL; it does not treat the
model prompt as the memory database. Every main-AI turn starts with a small,
indexed capsule and expands only when the capsule lacks evidence needed for the
current decision.

```text
PostgreSQL memory fabric
  raw messages + task history + locators + file index + world facts
                         |
            background conversation distillation
                         |
index capsule ── model requests `memory_expand` ──> focused capsule ──> deep capsule
```

## Tiers

| Tier | Default contents | When used |
| --- | --- | --- |
| `index` | One recent turn, active task, distilled summary, 5 files, 8 facts, 6 entities, open-question index | Every new main-AI run |
| `focused` | 4 turns, 10 files, 16 facts, 12 entities, relevant inferences | The model cannot resolve a prior decision from the index |
| `deep` | 8 turns, 20 files, 32 facts, 24 entities, wider uncertainty set | Focused recall still lacks necessary evidence |

The exact visible counts and an estimated token size are stored in
`memory_index`. The model may request the next tier only by returning:

```json
{
  "kind": "memory_expand",
  "arguments": {"depth": "focused"},
  "reason": "The index lacks the earlier migration decision."
}
```

The request is persisted as `memory_context_expanded`, so retries and later
turns use the same chosen tier rather than silently widening again.

## Database and distillation

- `lh_messages` retains raw conversation evidence.
- `lh_conversation_summaries` stores the background-generated durable focused
  summary, entities, relations, inferences and uncertainties.
- `lh_memory_distillation_layers` persists both the small `index` distillation
  and the fuller `focused` distillation. `deep` intentionally resolves through
  indexed primary evidence rather than another lossy summary.
- `lh_world_facts`, `lh_world_inferences` and `lh_world_uncertainties` retain
  evidence-bearing long-lived knowledge.
- `lh_files.search_vector` has a PostgreSQL GIN index. File recall now prefers
  that full-text index and falls back to ILIKE matching for CJK and punctuation
  cases.
- `lh_context_snapshots` caches each query *and memory tier* separately, so an
  index capsule can never be mistaken for a deep expansion.

## Boundaries

The tiered capsule controls memory visibility, not truth. Raw memory remains
available in the database, and a model must request a larger tier before asking
the user about prior work it has not yet checked. This does not automatically
reduce unrelated prompt costs such as the global capability map or active Mega
Project state; those are separate prompt-budget surfaces.
