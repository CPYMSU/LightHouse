# LightHouse OS 1.0

LightHouse is an autonomous PostgreSQL-first AI operating terminal. The main AI is
the highest-level decision maker: it understands the conversation, chooses tools,
acts directly, delegates through the Agent Bus, or combines both approaches.

The system provides reliable reality and execution evidence without replacing the
AI's semantic judgment with hard-coded workflows.

## Architecture

```text
User
  -> Main AI / Foreground Brain
       -> Context Intelligence
       -> Tool Knowledge Registry
       -> Direct capabilities
       -> Elastic Agent Bus
       -> Optional Mega Project knowledge space
  -> Thin Operation Kernel
  -> Data / System / Desktop executors
  -> durable Receipts

Background intelligence
  -> Memory Steward
  -> file and conversation distillation
  -> 24-neuron reflex field
  -> project knowledge convergence
  -> cached Context Snapshots
```

### Main AI

The main AI can freely decide to:

- execute a capability directly;
- search or inspect the persistent tool library;
- dispatch one or many specialist Work Orders;
- create an optional Mega Project knowledge space;
- continue investigation, form a plan, revise it, implement, test or regress;
- take over work from Agents;
- ask the user only when uncertainty materially requires it.

Tool recommendations and project-scale advice are advisory. No file-count or
keyword rule forces a workflow.

### Context Intelligence

Each model decision receives a compact evidence-rich bundle including:

- the current user request;
- recent complete conversation turns;
- older conversation summaries;
- active tasks and candidate entities;
- verified facts, inferences and uncertainties;
- recent files, locators and Receipts;
- available Agents and Work Orders;
- relevant tool recommendations;
- active Mega Project findings, steps and checkpoints;
- the latest completed background neuron snapshot.

Foreground reasoning does not run repository scans or neuron ABM processing.
Those tasks remain in background workers so the main AI can respond quickly.

### Tool Knowledge Registry

The Capability Atlas is synchronized into PostgreSQL. The main AI and authorized
Agents can rediscover tools instead of relying on prompt memory:

```text
tools.search.v1
tools.inspect.v1
tools.recommend.v1
```

The registry stores tool schemas, aliases, categories, risk, confirmation mode,
requirements, examples and relations.

### Mega Project tools

Large projects are supported through composable primitives rather than a fixed
state machine:

```text
project.create.v1
project.inspect.v1
project.checkpoint.v1
project.finding.store.v1
project.finding.search.v1
project.step.create.v1
project.step.update.v1
agent.bus.dispatch_many.v1
agent.bus.results.v1
```

Logical Work Orders have no product-level count ceiling. Physical concurrency is
controlled by durable Agent leases, provider capacity and PostgreSQL
`FOR UPDATE SKIP LOCKED` claims.

Findings distinguish verified facts, inferences, risks, constraints, unknowns,
conflicts and recommendations while retaining evidence and source Work Orders.

### 24-neuron reflex field

Database and memory changes create deterministic 64-dimensional stimuli. Twenty-
four adaptive neurons maintain independent state, memory and relationships. The
latest completed field is injected as reflex evidence for the main AI; it never
replaces verified facts or the main AI's decision authority.

### Operation Kernel

Every side effect still passes through typed capabilities and a thin deterministic
kernel that preserves:

- Workspace and Target authority;
- real address and path validation;
- immutable Operation arguments;
- scoped confirmation or Auto Mode authorization;
- idempotent execution claims;
- durable Receipts and transaction results.

The kernel validates reality and authority. It does not decide what the user means.

## Auto Mode

Auto Mode is enabled by default. Each durable Run shows one scoped authorization
card. After acceptance, governed Operations in that Run can continue without
repeated confirmation cards. The authorization ends when the Run succeeds, fails,
is cancelled, reaches its step limit, or waits for new user input.

```text
/auto on
/auto off
/auto status
```

Manual mode retains exact per-Operation confirmation.

## Multi-instance Kernel

Multiple local LightHouse API instances can share one PostgreSQL Data, Memory,
Neuron and Tool Kernel while keeping separate ports, project bindings, logs and
conversation pointers.

```text
lh new
lh new research --project /path/to/project
lh instances
lh attach research
lh stop research
lh start research
lh --instance research "continue the project"
```

## Install on macOS

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh | bash
```

The installer prepares Python 3.12, PostgreSQL 16 and Git, stores credentials in
macOS Keychain, installs the `launchd` service and `lh`, migrates the database and
runs health checks.

## Install on Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

The installer uses current-user DPAPI for credentials, preserves the database and
configuration during upgrades, stops stale LightHouse processes before replacing
the virtual environment, and registers the default Scheduled Task.

## Use

Open a project directory and run:

```text
cd /path/to/project
lh
```

Then describe the goal naturally:

```text
Investigate this repository, decide whether it needs Mega Project mode, form the
best implementation approach, complete the code, and design regression tests.
```

Useful terminal commands:

```text
/help
/new
/reindex
/status
/capabilities
/mode auto|system|data|desktop
/auto on|off|status
/exit
```

## Execution surfaces

- **Data Kernel** — PostgreSQL and structured business data.
- **System Kernel** — files, code, Git, tests, Bash/PowerShell and authorized
  remote Linux execution.
- **Desktop Kernel** — semantic macOS and Windows application, browser and file
  launching.

The filesystem and PostgreSQL remain sources of truth. Memory, indexes, neuron
state and project knowledge improve relevance and speed but cannot expand Target
authority.
