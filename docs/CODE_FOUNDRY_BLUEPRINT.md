# LightHouse CodeFoundry Blueprint

> **Status:** proposed architecture
> **Scope:** the native coding production line only. This document deliberately
> excludes client work and execution-security policy.

## 1. Decision

LightHouse will **not** embed, call, fork, or depend on the Codex CLI or
app-server at runtime. It will instead build a native Python coding runtime,
named **CodeFoundry**, whose public data model, persistence, tool contracts,
and model-provider interface belong to LightHouse.

OpenAI Codex is a source of implementation ideas, small reusable algorithms,
and tests—not an upstream runtime. The intent is to learn the production line
that makes a coding agent effective and make it LightHouse's own.

```text
Not this:
  LightHouse -> Codex app-server -> Codex does the work

This:
  LightHouse memory / task system
              |
              v
  LightHouse CodeFoundry (native code agent production line)
              |
              v
  LightHouse executors, Git workspace, tests, receipts
```

The model remains a replaceable dependency. A strong coding-capable model is
needed for strong coding output; copying an agent runtime does not copy model
weights or training. CodeFoundry's job is to give such a model the right
context, tool loop, and completion discipline.

## 2. Why a new inner loop is necessary

The current agent loop is intentionally general:

```text
complete state -> model returns one JSON decision -> one capability
-> Operation -> Receipt -> next model decision
```

That is a good governance loop, but it is an inefficient coding loop. A coding
agent must cheaply inspect multiple files, preserve a compact exact history of
what it learned, edit with a patch, inspect the resulting diff, choose relevant
tests, and use the outcomes to revise. Treating every small read as a new
general-purpose orchestration turn makes the model spend attention on the
framework instead of the code.

CodeFoundry creates a specialised inner loop while retaining the existing
LightHouse outer loop:

```text
Outer plane: task continuity, memory, project intent, durable accounting
                         |
                         v
Inner plane: code working set -> actions -> evidence -> verification -> result
```

Neither plane may silently replace the other. The outer plane supplies durable
intent; the inner plane supplies engineering throughput and proof.

## 3. Non-goals and hard boundaries

### In scope

- a LightHouse-owned coding state machine;
- a compact, typed coding-tool vocabulary;
- structured coding history and context compaction;
- parallel read-only exploration and ordered mutations;
- patch, diff, test, and review evidence;
- a reproducible evaluation suite for coding tasks;
- selective, traceable reuse of Apache-2.0 Codex source material.

### Explicitly out of scope

- Codex CLI, TUI, VS Code extension, app-server, or JSON-RPC protocol;
- copying Codex product interfaces, thread formats, or configuration format;
- porting the whole Rust workspace;
- model training, model weights, hosted Codex services, or client changes;
- a second memory system that competes with LightHouse Memory Fabric.

The key boundary is simple: **a CodeFoundry run is a LightHouse run, not a
Codex-shaped session.**

## 4. Target architecture

```text
              LightHouse task / memory / project intent
                                |
                                v
                         CodeBriefCompiler
                                |
                                v
 +-------------------------- CodeFoundry --------------------------+
 |  CodeRun -> CodeHistory -> CodeModelAdapter -> CodeActionBatch  |
 |                    ^                         |                  |
 |                    |                         v                  |
 |             ContextCompactor <--- CodeRuntime <--- ToolRegistry |
 |                                            |                     |
 |                                            v                     |
 |              EvidenceLedger -> VerificationGate -> CodeResult   |
 +------------------------------------------------------------------+
                                |
                                v
       LightHouse Operation Kernel / executors / Git / test commands
                                |
                                v
                         AgentRun steps and Receipts
```

### Ownership

| Layer | Owns | Does not own |
|---|---|---|
| `CodeBriefCompiler` | task contract and the code working set | global capability atlas |
| `CodeModelAdapter` | model wire translation | the CodeFoundry domain protocol |
| `CodeRuntime` | action scheduling, action results, workspace ordering | durable product memory |
| `EvidenceLedger` | machine-readable evidence | free-form agent claims |
| `VerificationGate` | whether a code run may finish | how a test command executes |
| existing LightHouse Kernel | target execution and durable operation receipts | code-agent reasoning |
| existing Memory Fabric | relevant prior decisions and learned facts | per-turn tool transcript |

## 5. Native domain contracts

CodeFoundry must use its own small contracts. They are deliberately independent
of Codex request, response, item, and app-server types.

```python
@dataclass(frozen=True)
class CodeAction:
    id: str
    kind: Literal[
        "search", "read", "list", "status", "patch", "diff", "test", "review"
    ]
    arguments: dict[str, Any]
    mutates_workspace: bool

@dataclass(frozen=True)
class CodeObservation:
    action_id: str
    kind: str
    ok: bool
    payload: dict[str, Any]
    started_at: datetime
    completed_at: datetime

@dataclass(frozen=True)
class CodeEvidence:
    kind: Literal["patch", "diff", "test", "review", "fact"]
    observation_ids: tuple[str, ...]
    digest: str
    summary: dict[str, Any]

@dataclass(frozen=True)
class CodeResult:
    status: Literal["verified", "needs_input", "failed", "unverified"]
    changed_paths: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    summary: str
```

`CodeAction` is the only planning vocabulary visible to the coding model. It
starts with eight actions because a small, familiar tool set is easier for a
coding model to use correctly than the entire LightHouse capability atlas.
Additional tools are exposed only through explicit progressive discovery, not
by preloading unrelated database, desktop, or project-administration tools.

## 6. The production-line state machine

```text
created
  -> brief_ready
  -> inspecting
  -> editing
  -> validating
  -> reviewing
  -> verified | needs_input | failed | unverified
```

Transitions are evidence driven:

1. **Create brief.** Build the task contract and exact working set.
2. **Inspect.** The model may issue an action batch containing independent
   `status`, `search`, `list`, and `read` actions.
3. **Edit.** A successful `patch` creates a dirty-change marker and records
   the changed paths.
4. **Validate.** A dirty-change marker requires a post-patch `diff` and at
   least one selected validation action before `verified` is possible.
5. **Review.** A deterministic diff review always runs for a changed tree;
   model review is added for multi-file, behavioural, or release work.
6. **Finish.** `VerificationGate` computes the status from evidence. A model's
   final prose is a summary, never proof.

There is no repeated-guidance escape hatch. If a code run still lacks required
evidence, it stays in `validating` or ends as explicitly `unverified`; it never
becomes verified merely because it has asked to revise before.

### Required evidence graph

```text
patch
  |
  +-> post-patch diff ----------+
  |                              |
  +-> selected test / validation +-> deterministic review -> verified result
```

For a read-only task, the graph reduces to inspected sources plus the requested
answer. For a code-changing task, all three branches are required. A test that
predates the patch, or a diff from before the final patch, cannot satisfy the
graph.

## 7. CodeBriefCompiler: build a coding working set, not a project dump

The initial model context must be a concise, task-specific brief:

```text
CodeBrief
  - task and explicit acceptance criteria
  - repository root, current Git status, current branch
  - repository instructions (AGENTS.md, AGENTS.override.md, LIGHTHOUSE.md)
  - relevant durable facts from Memory Fabric
  - likely entry points, named symbols, direct callers, and nearest tests
  - existing diff when the tree is already dirty
  - known test commands and language/toolchain hints
  - outstanding uncertainty
```

It must not begin with the full agent topology, all registered capabilities, or
unrelated memory. `CodeBriefCompiler` may request more context through normal
actions when the initial hypothesis is wrong.

The compiler should reuse existing `system.project.context.v1` output,
`context_intelligence.py`, and `cognitive_projection.py` as input sources. It
must produce a new, code-specific projection rather than change their existing
general-purpose semantics.

## 8. Tool runtime and batching

### Scheduling rules

| Action class | Examples | Scheduling |
|---|---|---|
| independent reads | `search`, `read`, `list`, `status` | execute concurrently in a batch |
| dependent reads | read a path found by search | next batch |
| mutation | `patch` | one workspace mutation at a time |
| validation | `diff`, `test`, deterministic review | after the relevant mutation |
| model review | semantic review of final diff | after deterministic review |

The runtime has a per-worktree mutation sequence but no artificial serialization
of independent reading. `asyncio.TaskGroup` is sufficient for the first Python
implementation; the execution surface remains LightHouse-owned.

### Adapter to the current kernel

CodeFoundry actions adapt to existing capabilities in the first release:

| CodeFoundry action | Existing capability |
|---|---|
| `status` | `system.git.status.v1` |
| `search` | `system.file.search.v1` |
| `read` | `system.file.read.v1` |
| `patch` | `system.file.patch.v1` |
| `diff` | `system.git.diff.v1` |
| `test` | `system.test.run.v1` |
| `review` | deterministic diff analyser, then optional model review |

The adapter creates normal LightHouse Operations and Receipts. It adds a
CodeFoundry observation with the Operation ID and receipt digest, so neither
system has a competing source of truth. A later performance pass may add a
batch-oriented kernel entry point; it must preserve these same receipts.

## 9. History and context compaction

`CodeHistory` is a separate, typed event ledger. It stores the exact task
brief, actions, observations, evidence, model request identifiers, and bounded
model-facing output. It does not store a repeated serialization of all
LightHouse state on every turn.

Compaction follows these rules:

1. Never discard the task contract, repository instructions, dirty-change
   marker, changed-path set, or required evidence state.
2. Keep the latest useful observation for each path or query; replace stale
   observations only when a mutation invalidates them.
3. Bound large search, test, and command output into a stable head/tail plus
   summary structure, retaining the full original in the Receipt.
4. Reinject a context delta when working-tree state, visible tools, or task
   requirements change.
5. Trigger a deliberate model summarisation turn before the context budget
   becomes exhausted; never silently truncate essential facts.

This makes context a maintained engineering artefact instead of an ever-growing
chat transcript.

## 10. Model integration without adopting a Codex interface

The new provider contract is native to LightHouse:

```python
class CodeModelAdapter(Protocol):
    def respond(
        self,
        *,
        instructions: str,
        brief: CodeBrief,
        history: tuple[CodeHistoryItem, ...],
        tools: tuple[CodeToolSpec, ...],
    ) -> CodeModelResponse: ...
```

`CodeModelResponse` contains either a batch of `CodeAction` values, a concise
request for user input, or a final summary. Provider-specific implementations
translate that contract to a model's native tool/function mechanism. The
existing `OpenAICompatibleProvider` remains available for general operations;
it is not forced to carry CodeFoundry's high-frequency loop.

The first adapter may support a single provider, but the domain contract must
remain provider neutral. Do not reproduce Codex's app-server, Responses items,
or CLI configuration formats in the LightHouse database.

## 11. Persistence and observability

Add migration `0010_code_foundry.sql` and repository methods for these durable
records:

| Record | Required fields | Purpose |
|---|---|---|
| `lh_code_runs` | run ID, parent AgentRun ID, workspace, state, task digest, config | recover the code state machine |
| `lh_code_turns` | turn ID, code run ID, sequence, prompt digest, model metadata, status | account for each model decision |
| `lh_code_items` | item ID, turn ID, action/observation type, payload digest, timings | reconstruct the native coding transcript |
| `lh_code_evidence` | evidence ID, kind, observation IDs, digest, validity state | drive verification mechanically |
| `lh_code_evals` | case ID, revision, metrics, verdict | compare production-line changes |

Payloads required for recovery remain in the existing event/receipt storage;
the CodeFoundry tables index them and preserve their relationships. Every
`CodeObservation` must link to an Operation ID when it used a kernel executor.

Useful production metrics are:

- model turns and actions per completed task;
- parallel read batch width and elapsed time;
- time from first patch to passing validation;
- changed-path count and diff size;
- test selection, pass/fail, and post-patch freshness;
- verification-gate failure reason;
- evaluation pass rate and regression rate.

## 12. Source-lineage and reuse policy

The reference snapshot is:

```text
openai/codex commit 61a44880a85d2fd0d8770908dea5733495e571c8
Apache-2.0; snapshot date 2026-07-26
```

Codex is studied source-by-source. Directly adapted files must retain the
appropriate Apache-2.0 copyright and notice information; a provenance entry
must identify the upstream path, commit, adaptation date, and local tests.
Before the first literal source reuse, update `NOTICE.md` and add the required
third-party notice material. This blueprint alone does not vendor Codex code.

| Upstream Codex source | Native LightHouse destination | Reuse mode | First acceptance test |
|---|---|---|---|
| `codex-rs/core/src/context_manager/history.rs` | `code_foundry/history.py` | semantic port of typed history, normalization, bounded outputs, state diffs | stale observations disappear after a patch; pinned evidence remains |
| `codex-rs/core/src/context/world_state/tools.rs` | `code_foundry/tool_context.py` | design port for incremental tool visibility | only changed tool visibility is reinjected |
| `codex-rs/core/src/tools/registry.rs` | `code_foundry/tools.py` | semantic port of typed registry and per-tool metadata | hidden tools cannot be selected; read tools advertise batchability |
| `codex-rs/core/src/tools/parallel.rs` | `code_foundry/runtime.py` | algorithmic port of batch lifecycle, cancellation, timing | independent reads overlap; writes preserve order |
| `codex-rs/core/src/tools/handlers/apply_patch.rs` | `code_foundry/patch.py` | patch-result normalization; executor remains LightHouse native | patch receipt identifies changed paths and invalidates old evidence |
| `codex-rs/core/src/session/turn.rs` | `code_foundry/loop.py` | turn loop, tool/result sequencing, pre-sampling compaction | every model turn receives typed action results, not raw global state |
| `codex-rs/core/gpt_5_codex_prompt.md` | `code_foundry/instructions.py` | behavioural requirements rewritten for LightHouse | instructions prioritise inspect, minimal change, diff, tests |

Do not copy Codex app-server, CLI, TUI, cloud-task, account, or UI sources.
They do not improve LightHouse's native coding production line and would create
unwanted protocol and maintenance coupling.

## 13. Module plan

```text
src/lighthouse/code_foundry/
  __init__.py
  models.py          # CodeRun, CodeAction, observation, evidence, result
  brief.py           # CodeBriefCompiler
  tools.py           # CodeActionRegistry and tool specifications
  runtime.py         # batches, ordering, operation adapter, timings
  history.py         # typed history, invalidation, compaction inputs
  loop.py            # CodeFoundry state machine
  provider.py        # CodeModelAdapter and provider translations
  patch.py           # changed-path extraction and patch normalisation
  evidence.py        # evidence graph construction
  verification.py    # hard completion gate
  review.py          # deterministic diff review and optional model review
  store.py           # repository adapter and persistence records
  evals.py           # fixture runner and metrics
  instructions.py    # concise coding-only operating instructions

tests/
  test_code_foundry_models.py
  test_code_foundry_brief.py
  test_code_foundry_runtime.py
  test_code_foundry_history.py
  test_code_foundry_evidence.py
  test_code_foundry_verification.py
  test_code_foundry_provider.py
  test_code_foundry_evals.py
```

Existing files remain responsible for their current concern:

| Existing file | CodeFoundry integration |
|---|---|
| `bootstrap.py` | wires CodeFoundry behind an explicit configuration switch |
| `agent.py` / `engineering.py` | routes a coding task into CodeFoundry and receives `CodeResult` |
| `kernel.py` | executes adapted actions and persists Operations / Receipts |
| `context_intelligence.py` | supplies durable facts to `CodeBriefCompiler` |
| `cognitive_projection.py` | retains general projection; no longer serves as the coding transcript |
| `work_intensity.py` | supplies budget and review depth policy, not completion bypasses |
| `repository.py` / `agent_store.py` | add durable CodeFoundry records and recovery methods |

## 14. Delivery sequence

### Phase 0 — Baseline and contracts

- Freeze 20–50 representative LightHouse coding tasks as evaluation fixtures.
- Record the current loop's success, diff, test, turn, token, and elapsed-time
  metrics.
- Add the domain models, schema migration, and repository tests without routing
  live tasks yet.

**Exit condition:** fixture runner produces a baseline report and all current
tests remain green.

### Phase 1 — Native evidence and verification

- Implement `CodeAction`, `CodeObservation`, `CodeEvidence`, and
  `VerificationGate`.
- Add changed-path tracking and post-patch evidence freshness.
- Replace the repeated-guidance `pass_with_warning` path for CodeFoundry runs
  with explicit `unverified` or continued revision.

**Exit condition:** a changed run cannot report `verified` without a current
diff and selected post-patch validation.

### Phase 2 — Code working set and typed history

- Implement `CodeBriefCompiler` from project context, relevant memory, Git,
  repository instruction files, symbols, callers, and nearby tests.
- Implement `CodeHistory` and deterministic compaction / invalidation.
- Keep the existing general context system unchanged.

**Exit condition:** model prompts contain the task-specific working set and no
longer require the full generic capability atlas for ordinary code work.

### Phase 3 — Tool registry and runtime

- Introduce the eight-action registry and action-to-capability adapter.
- Add read-only action batching, timing, observation normalization, and
per-worktree mutation ordering.
- Preserve normal Operation IDs and Receipts for every executor action.

**Exit condition:** a code task can perform parallel exploration, make a patch,
and produce a complete evidence graph using only native CodeFoundry contracts.

### Phase 4 — Native model loop

- Implement `CodeModelAdapter` and provider-specific structured tool calling.
- Implement the CodeFoundry turn loop, action-batch parser, retries, and
pre-sampling compaction.
- Route coding tasks through a feature flag: `off`, `shadow`, then `on`.

**Exit condition:** `shadow` runs produce complete transcripts and evidence
without changing the authoritative legacy result; `on` completes selected
fixtures end to end.

### Phase 5 — Review, evaluation, and rollout

- Add deterministic diff review, optional model review, and test-selection
records.
- Compare CodeFoundry versus the baseline on the frozen fixture suite.
- Promote only when it improves verified completion rate without widening diffs
or increasing validation failures.

**Exit condition:** a published evaluation report supports making CodeFoundry
the default coding loop; the legacy generic loop remains available for
non-coding operations.

## 15. Evaluation contract

Every fixture defines:

```yaml
id: fix-parser-null-input
repository: fixtures/parser-service
task: Reject null parser input with the existing error contract.
acceptance:
  - target test passes
  - no unrelated files change
  - public error text remains stable
expected_evidence:
  - patch
  - diff
  - test
```

Metrics are reported per fixture and in aggregate. The minimum release bar is:

1. 100% of code-changing completed runs have current patch, diff, and validation
   evidence.
2. No fixture can become `verified` after a failed required validation.
3. Current LightHouse unit tests stay green.
4. CodeFoundry's verified fixture completion rate is measured against the
   frozen baseline before it becomes the default.
5. Every direct upstream-source adaptation has a provenance entry and local
   regression test.

## 16. Decisions to make before implementation

The implementation can begin without choosing a new client or external Codex
interface. The only product decisions needed are:

1. Which coding-capable model and provider are the initial `CodeModelAdapter`?
2. Should Phase 0 fixtures live inside this repository, or in a separate
   versioned evaluation repository?
3. Which task classes use CodeFoundry first: bug fixes, feature work, code
   review, or all source-changing tasks?

Until those choices are made, Phases 0–3 are provider-neutral and can proceed.

## 17. Definition of success

CodeFoundry succeeds when a LightHouse coding task behaves like a disciplined
engineering session:

```text
understand the local code -> inspect relevant facts -> change minimally
-> prove the final diff -> run relevant validation -> report evidence
```

At that point LightHouse will not be a wrapper around Codex. It will be a
native production system with a coding loop informed by Codex's strongest open
source design ideas and verified by LightHouse's own receipts, memory, and
evaluation suite.
