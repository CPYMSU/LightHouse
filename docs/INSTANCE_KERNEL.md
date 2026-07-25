# LightHouse Instance Kernel

LightHouse 1.2.0 can run multiple local API instances at the same time without
splitting the durable Data, Memory, Neuron, Tool, Agent, Token, Mega Project or
Massive Build state.

## Isolation model

Every instance has its own:

- loopback API port;
- configuration file;
- server logs;
- project and Workspace binding;
- active conversation pointer;
- process lifecycle record;
- Lazy Auto preference.

All instances reuse the installed LightHouse runtime, native secret store and the
same PostgreSQL database. Receipts, long-term memory, neuron state, tool knowledge,
Agent Work Orders, Token receipts, Build Cells, contracts, leases, integrations,
Mega Project findings, Workspaces and background queues remain one coherent system.

PostgreSQL Work Orders and background jobs use transactional claims, including
`FOR UPDATE SKIP LOCKED`, so multiple API processes do not execute one queued item
twice. Database migrations acquire a PostgreSQL advisory transaction lock so two
instances cannot race the same DDL upgrade.

Instance records live under:

```text
~/.lighthouse/instances/
├── default/instance.json
├── research/
│   ├── config.json
│   ├── instance.json
│   └── logs/
└── coding/
    ├── config.json
    ├── instance.json
    └── logs/
```

The default instance remains owned by `launchd` on macOS or the `LightHouse`
Scheduled Task on Windows. Additional instances are detached local processes
managed by the `lh` command.

## Commands

```text
lh new
lh new research
lh new coding --project /path/to/project
lh new warehouse --project C:\work\warehouse
lh new research --no-attach

lh instances
lh attach research
lh stop research
lh start research
lh --instance research doctor
lh --instance research "continue the current task"
```

Lazy Auto is stored in each instance configuration, but it only controls whether an
action-time permission card offers **Auto-approve this Run**. The authority itself
belongs to the exact Run, actor, Workspace, target, capability scope and allowed
roots. Other instances and Runs do not inherit it.

```text
lh --instance research auto on
lh --instance research "complete the full research workflow"
```

## Shared intelligent state

Instances share:

- conversations, tasks, locators and Context Snapshots;
- Agent Registry, professional roles, Work Orders, progress and events;
- Token usage by Run, conversation, Agent, Work Order and project;
- Tool Knowledge Registry and research capabilities;
- Mega Project findings, steps, decisions and checkpoints;
- Massive Build Cells, contracts, Worktrees, Write Leases, batches and integrations;
- full-stack wiring evidence;
- 24-neuron state, memories, weights and ABM outcomes;
- Operations, confirmation state and Receipts.

An instance can continue a project started by another instance without copying or
fragmenting long-term state. Physical Agent concurrency is coordinated globally
through leases even when more than one instance runs specialist worker pools.

## Port allocation

The default preferred port is `8787`. Installation and `lh new` scan upward until
a free loopback port is found. A conflicting port is an allocation signal, not an
installation failure.

The chosen port is written once to that instance's config and becomes the single
source of truth for the API server, CLI, health checks and diagnostics.

## Safety boundary

- Every API binds to `127.0.0.1` by default.
- Control and model credentials remain in macOS Keychain or Windows DPAPI.
- Instance configs contain no model API key.
- Additional instances cannot expand System or Desktop Target roots.
- Tool, Agent and project recommendations cannot expand authority.
- Public research rejects private, loopback, link-local and redirect-to-private destinations.
- Specialist writes require compatible parent-Run authority; Massive Build writes
  also require a valid non-overlapping Write Lease.
- Stopping one additional instance does not stop PostgreSQL or another instance.
- Uninstallers stop registered additional processes before removing the runtime.
