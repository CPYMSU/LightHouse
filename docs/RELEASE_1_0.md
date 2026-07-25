# LightHouse OS 1.0.0

LightHouse 1.0 establishes the first complete product baseline for an autonomous,
terminal-grade AI operating system.

## Product baseline

LightHouse 1.0 combines:

- a trusted Main AI / Foreground Brain with direct, delegated and hybrid execution;
- Context Intelligence with recent complete turns, durable summaries, facts,
  inferences, uncertainties and cached snapshots;
- PostgreSQL-first Data, Memory, Tool, Agent, Neuron and Mega Project state;
- a durable Agent Bus with elastic logical Work Orders and lease-based physical
  concurrency;
- a persistent Tool Knowledge Registry that lets the AI rediscover capabilities;
- optional Mega Project knowledge spaces for findings, evidence, steps, decisions
  and checkpoints;
- a 24-neuron adaptive reflex field used as background cognitive evidence;
- multi-instance local runtimes sharing one coherent durable state;
- one-confirmation-per-Run Auto Mode;
- a thin Operation Kernel with typed capabilities, immutable arguments,
  idempotency and durable Receipts.

## Mega Project support

Mega Project support is a tool ecosystem, not a hard-coded workflow. The main AI
may freely:

- create or ignore a project container;
- investigate directly or dispatch any number of logical specialist Work Orders;
- store verified facts, inferences, risks, constraints, unknowns, conflicts and
  recommendations with evidence;
- continue collecting information, form or revise a plan, execute steps, design
  tests, run regression, roll back, or take over Agent work;
- decide when the available knowledge is sufficient.

The durable queue controls actual simultaneous execution, so logical scale does
not become uncontrolled model or database concurrency.

## Performance boundary

Repository indexing, conversation distillation and neuron ABM processing remain in
background workers. Foreground Context compilation reads cached and latest
completed snapshots so the main AI does not wait for those jobs before every
reply.

## Upgrade

The platform installers preserve existing configuration, credentials, PostgreSQL
data, conversations, Work Orders, neuron state and project knowledge.

macOS:

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh | bash
```

Windows PowerShell:

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

After installation, verify:

```text
lh doctor
```

The Swiss terminal masthead and `/healthz` report version `1.0.0`.
