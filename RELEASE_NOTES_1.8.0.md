# LightHouse OS 1.8.0

## Memory Resolution / 分級記憶解析

Every new main-AI run now begins with a compact `index` memory capsule instead
of receiving a broad recent-history dump. The capsule contains the active task,
one recent turn, a distilled summary, a small set of facts/files/entities, and
a durable `memory_index` showing what remains available in PostgreSQL.

- The model can return `memory_expand` with `focused` or `deep` only when the
  current capsule lacks necessary prior evidence. It checks the next memory
  tier before asking the user to repeat earlier work.
- Expansion choice is persisted in the run ledger, so retries retain the same
  evidence boundary instead of silently widening prompt context.
- Conversation summaries, world facts, inferences, uncertainties, file
  locators, and raw messages stay in Memory Fabric; only a token-budgeted view
  crosses into the model prompt.
- `lh_memory_distillation_layers` now persists the compact `index` and fuller
  `focused` conversation distillations; `deep` resolves indexed primary
  evidence rather than introducing another lossy summary.
- File recall now prioritizes the existing PostgreSQL GIN full-text index and
  retains an ILIKE fallback for CJK and punctuation search behavior.
- Context snapshots are keyed by memory tier, preventing an index capsule from
  being confused with a deeper recall result.

## Verification / 驗證

- Added capsule-budget and progressive-expansion tests.
- Full suite: 226 passed, 15 skipped.

## Boundary

This release reduces memory-context cost. Large capability maps and Mega
Project state are separate prompt-budget surfaces and are not hidden by memory
distillation.
