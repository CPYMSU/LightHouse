# LightHouse OS 1.2

LightHouse is an autonomous PostgreSQL-first AI operating terminal. The main AI is
the highest-level decision maker: it understands the conversation, acts directly,
delegates to specialist Agents, waits for results, works in parallel, reviews them
later, or combines these strategies without a fixed workflow.

The system provides tools, context, evidence and safe execution primitives. It does
not replace the main AI's semantic judgment with keyword routing or mandatory steps.

## Architecture

```text
User
  -> Main AI / Project Director
       -> Context Intelligence + Tool Knowledge Registry
       -> Direct capabilities
       -> Observable, elastic Agent Bus
       -> Optional Mega Project / Massive Build knowledge space
  -> Thin Operation Kernel
  -> Data / System / Desktop / Research executors
  -> immutable Operation Receipts

Background intelligence
  -> Memory Steward
  -> Research / Taste / Frontend / Backend Agents
  -> Wiring / Integration / Test / Contract Agents
  -> 24-neuron reflex field
  -> cached Context and project distillation
```

## Trusted main AI

The main AI can freely decide to:

- execute a capability directly;
- search or inspect the persistent tool library;
- dispatch one or thousands of logical specialist Work Orders;
- wait for all Agents, wait only for critical Agents, continue immediately, or
  implement in parallel and review distilled results later;
- create Build Cells, contracts, Worktrees, write leases, code batches and
  integrations for very large projects;
- revise the project direction when new evidence arrives;
- ask the user only when evidence or authority genuinely requires it.

Agent Bus coordination advice is advisory. No file count, keyword or phase machine
forces the main AI to wait, plan, build or test in a particular order.

## Agent Observatory

LightHouse 1.2 makes delegated intelligence visible. During a Run the terminal shows:

- total, active, queued and completed Agents;
- each Agent's role, current task, progress and status;
- critical findings and Agent Bus waiting advice;
- this-turn and conversation Token usage;
- whether Token values came from the provider or local estimation.

```text
/agents
/tokens
```

Professional roles include:

```text
research
taste
frontend
backend
wiring-verification
integration
test-design
contract
```

Research Agents can use bounded public-web research tools. Taste Agents review
hierarchy, typography, grid, spacing, color and generic AI-template patterns.
Backend and Wiring Verification Agents distinguish static demos, mock-connected UI,
API-connected features, database-connected features and Receipt-verified behavior.

## Lazy Auto Mode

Normal conversation, explanation, research, context loading and read-only Agent work
do not display an Auto Mode card.

When the first governed side effect actually needs permission, the terminal offers:

```text
[once] Allow once
[auto] Auto-approve this Run
[deny] Deny
```

A Run-scoped Auto grant is tied to the actor, Workspace, target, capability class and
allowed roots. Scope expansion asks again. `/auto on` controls whether the action-time
card offers the Run-scoped Auto choice; it never pre-authorizes every conversation.

```text
/auto on
/auto off
/auto status
```

## Receipt-preserving failure semantics

Execution outcome and natural-language response outcome are independent. If a file
was written or opened successfully and the model provider disconnects afterward,
LightHouse preserves the successful Receipt and returns:

```text
COMPLETED_WITH_WARNING
execution_status = succeeded
response_status = provider_failed
```

A provider failure can no longer erase a verified real-world operation.

## Token receipts

Every main-AI, Memory Steward and specialist-Agent model call can persist:

```text
input_tokens
output_tokens
cached_input_tokens
reasoning_tokens
total_tokens
model / provider
Run / conversation / Work Order / project
```

Provider-reported usage is preferred. When a provider omits usage, LightHouse stores
a bounded local estimate and marks it as estimated instead of presenting it as exact.

## Tool Knowledge Registry

The Capability Atlas is synchronized into PostgreSQL so the main AI and authorized
Agents can rediscover tools instead of relying on prompt memory:

```text
tools.search.v1
tools.inspect.v1
tools.recommend.v1
```

The registry stores schemas, aliases, categories, risk, confirmation mode,
requirements and examples.

## Massive Build Mode

Very large projects, including projects that cumulatively create or change tens of
thousands of lines, are built from reviewable and independently verifiable units.
They are not produced as one unreviewable model response.

```text
Build Cells
  -> versioned shared contracts
  -> isolated Git Worktrees when useful
  -> non-overlapping expiring Write Leases
  -> reviewable code batches
  -> incremental domain and project integrations
  -> continuous focused and regression testing
  -> full-stack wiring evidence
```

Key tools include:

```text
project.cell.create.v1
project.contract.create.v1
project.write_lease.acquire.v1
project.worktree.create.v1
project.batch.create.v1
project.batch.update.v1
project.integration.create.v1
project.integration.update.v1
project.wiring.verify.v1
agent.bus.wait_many.v1
agent.bus.events.v1
agent.bus.coordination.v1
```

Logical Work Orders have no product-level count ceiling. Physical concurrency is
controlled by configurable worker pools, per-role Agent capacity, durable leases and
PostgreSQL `FOR UPDATE SKIP LOCKED` claims. The default installation runs up to eight
specialist workers and can be adjusted with `LIGHTHOUSE_AGENT_WORKERS` up to 64.

Massive Build does not impose a fixed investigation, planning, implementation or test
sequence. The main AI can create only the tools and coordination structures useful
for the current project.

## Full-stack truth

Feature wiring can be persisted across:

```text
UI -> event -> API -> service -> repository -> database -> Receipt -> E2E
```

A feature is not described as real or fully connected unless suitable evidence exists.
Static or mock dashboards remain explicitly labelled as such.

## Context Intelligence

Each main-AI decision receives a compact evidence-rich bundle containing recent
complete conversation turns, older summaries, active tasks, verified facts,
uncertainties, relevant files and Receipts, available tools, Agent state, coordination
advice, project findings, Build Cells, contracts, write leases, integration state,
wiring evidence and the latest completed neuron snapshot.

Foreground reasoning does not synchronously scan repositories or process neuron ABM
work. Those tasks remain in background workers.

## Operation Kernel

Every side effect still passes through typed capabilities and the thin deterministic
Kernel, preserving:

- Workspace and Target authority;
- real address and allowed-root validation;
- immutable Operation arguments;
- action-time confirmation and scoped Auto authority;
- idempotent execution claims;
- durable Receipts and database transaction results.

The Kernel validates reality and authority. It does not decide what the user means.

## Multi-instance Kernel

Multiple local LightHouse instances share one PostgreSQL Data, Memory, Neuron, Tool,
Agent and Massive Build state while keeping separate ports, project bindings, logs and
conversation pointers. Database migrations are serialized with a PostgreSQL advisory
transaction lock.

```text
lh new research --project /path/to/project
lh instances
lh attach research
lh stop research
lh start research
```

## Install on macOS

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh | bash
```

## Install on Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

The installers preserve PostgreSQL data, configuration and native secret storage
during upgrades.

## Use

```text
cd /path/to/project
lh
```

Then describe the goal naturally:

```text
Investigate this repository, use as many Agents and Build Cells as useful, decide
whether to wait for their results or work in parallel, implement the complete system,
verify every frontend-to-database path, and finish regression testing.
```

Useful commands:

```text
/help
/new
/reindex
/status
/agents
/tokens
/capabilities
/mode auto|system|data|desktop
/auto on|off|status
/exit
```

The filesystem and PostgreSQL remain sources of truth. Memory, indexes, Agent results,
neuron state and project knowledge improve relevance and speed but cannot expand
Target authority.
