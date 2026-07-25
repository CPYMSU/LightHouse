# Agent Observatory and Massive Build

LightHouse OS 1.2 adds observable specialist collaboration and a real coordination
model for projects that cumulatively create or modify thousands or tens of thousands
of lines of code.

## Main AI freedom

The Agent Bus may recommend waiting when critical findings are pending. The main AI
can still choose any of these strategies:

```text
wait_for_all
wait_for_critical
continue_without_waiting
work_and_review_later
```

The recommendation is context evidence, not a workflow transition. Research,
implementation and verification can run concurrently, and the main AI can review
newly distilled findings at any later checkpoint.

## Observable Agents

The terminal and API expose durable Work Order state:

```text
queued
leased
running
waiting_dependency
waiting_confirmation
succeeded
failed
cancelled
superseded
```

Each Work Order can report a display summary, progress, Token usage and attention
level. Attention levels are background, checkpoint, important and critical.

```text
/agents
/tokens
```

API surfaces:

```text
GET /v1/agent/runs/{run_id}/agents
GET /v1/agent/runs/{run_id}/usage
GET /v1/agent-bus/work-orders/{work_order_id}/events
GET /v1/agent-bus/coordination
```

## Professional Agents

- **Research** — current mature approaches, sources, implementation patterns and
  technical tradeoffs.
- **Taste** — hierarchy, grid, typography, spacing, information density, color and
  generic AI-template detection.
- **Frontend** — frontend architecture, interaction, accessibility and browser
  behavior.
- **Backend** — APIs, services, repositories, transactions and persistence.
- **Wiring Verification** — UI through database, Receipt and E2E evidence.
- **Integration** — Build Cell integration and contract or merge conflict analysis.
- **Test Design** — regression coverage from changed behavior and failure modes.
- **Contract** — versioned API, data, event, capability and UI contracts.

Agents can request only tools registered to their role. Read-only work can proceed in
the background. Side effects require compatible parent-Run authority; Massive Build
writes also require a valid Write Lease.

## Logical scale and physical concurrency

Logical Work Orders have no product-level count ceiling. Physical execution remains
bounded by reality:

- worker pool size;
- per-role `max_concurrency`;
- provider capacity;
- Work Order dependencies;
- expiring leases;
- PostgreSQL `FOR UPDATE SKIP LOCKED` claims;
- non-overlapping write scopes.

The default worker pool is eight and can be configured:

```text
LIGHTHOUSE_AGENT_WORKERS=16
```

The runtime caps one instance at 64 workers. Multiple instances share the same queue
and cannot claim one Work Order twice.

## Build Cells

A Build Cell is a dynamic project unit with one goal and an independently verifiable
output. It may contain any combination of Research, Taste, Frontend, Backend, Test,
Contract, Wiring or Integration Work Orders.

Cells can be split by service, domain, capability, UI feature, data flow, risk or any
other boundary chosen by the main AI.

```text
project.cell.create.v1
project.cell.update.v1
project.massive.inspect.v1
```

## Contracts

Shared contracts make parallel implementation converge without forcing the whole
project to stop:

```text
draft
provisional
stable
deprecated
superseded
```

A provisional contract can support speculative work. A new version can supersede an
older version while preserving history and evidence.

```text
project.contract.create.v1
project.contract.inspect.v1
```

## Write Leases

Write Leases protect overlapping repository regions. Agents may read the same code,
but parallel writes to overlapping scopes are rejected until the first lease is
released or expires.

```text
project.write_lease.acquire.v1
project.write_lease.release.v1
```

Lease scopes can represent a repository, directory, module, file, symbol, API
namespace or migration range.

## Worktrees

When useful, a Build Cell can receive an isolated Git Worktree. Worktree operations
are explicit-confirmation capabilities, remain inside System Target roots and are
recorded in PostgreSQL.

```text
project.worktree.create.v1
project.worktree.remove.v1
```

Worktrees are optional. The main AI can choose serial edits or another safe strategy.

## Code batches and integration

Tens of thousands of lines emerge from reviewable batches rather than one giant
model output:

```text
project.batch.create.v1
project.batch.update.v1
project.integration.create.v1
project.integration.update.v1
```

A batch records changed files, line counts, behavioral summary, Receipts and focused
verification. Integrations record source Cells and batches, conflicts, result commit
and integration tests. Domain integrations can converge before the project-wide
release candidate.

## Full-stack wiring

Wiring evidence distinguishes static or mock UI from real system behavior:

```text
frontend
event
api
service
repository
database
receipt
e2e
overall
```

```text
project.wiring.verify.v1
```

The main AI should not describe a feature as fully connected until the relevant path
has evidence. Wiring Verification Agents are read-oriented by default and do not
repair code unless the main AI creates a separate authorized Work Order.

## Token receipts

Model usage is persisted by Run, conversation, Work Order, Agent and project.
Provider-reported usage is exact; locally estimated usage is marked.

Token visibility is for observation and optimization. It is not a hard project wall.
The main AI may cancel duplicates, increase distillation or continue when the value
of more work justifies it.
