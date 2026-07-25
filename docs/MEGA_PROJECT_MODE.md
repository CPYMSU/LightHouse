# LightHouse Mega Project Tool Ecosystem

Mega Project support is a set of composable tools, not a mandatory workflow.
The main AI remains Project Director and may investigate, plan, execute, revise,
run tests, dispatch specialists, or stay in direct mode according to its own
contextual judgment.

## Core contract

```text
Tool Registry remembers available capabilities
  -> Context Intelligence recommends relevant tools
  -> main AI chooses or ignores those recommendations
  -> Agent Bus creates durable logical Work Orders
  -> project findings and evidence converge in PostgreSQL
  -> main AI decides whether to investigate, plan, execute or revise
  -> Operations and Receipts preserve execution truth
```

No file-count threshold forces Mega Project mode. A large repository can still be
handled directly when the requested change is isolated. A small repository may
benefit from a project knowledge space when the work crosses security, persistence,
deployment or other high-uncertainty boundaries.

## Tool discovery

The Capability Atlas is synchronized into `lh_tools`. The main AI and authorized
Agents can use:

```text
tools.search.v1
tools.inspect.v1
tools.recommend.v1
```

Recommendations include relevance, risk and estimated execution character. They
are advisory only. `tools.search.v1` remains available when the model does not
remember a tool name.

## Project knowledge

Optional project containers preserve:

- verified facts, inferences, risks, constraints, unknowns and conflicts;
- raw evidence and source Work Orders;
- freely ordered project steps and dependencies;
- implementation Receipts and verification results;
- decisions and checkpoints.

The first capability set is:

```text
project.create.v1
project.inspect.v1
project.checkpoint.v1
project.finding.store.v1
project.finding.search.v1
project.step.create.v1
project.step.update.v1
```

Status and phase are descriptive state chosen by the main AI. The database does
not impose investigation -> planning -> implementation transitions.

## Elastic Agents

`agent.bus.dispatch_many.v1` accepts a main-AI-defined array of Work Orders. There
is no product-level maximum for the logical Agent population. Work Orders remain
queued in PostgreSQL and physical concurrency is controlled by worker leases,
Agent health, provider capacity and `FOR UPDATE SKIP LOCKED` claims.

```text
unbounded logical work graph
  !=
unbounded simultaneous model calls
```

`agent.bus.results.v1` returns a batch of durable states and results for synthesis.
The main AI may expand, cancel, supersede or ignore investigation branches.

## Auto Mode

LightHouse 0.9 Auto Mode can authorize one durable Run to continue through its
Operations without repeated cards. Mega Project tools do not expand that authority:
all executable side effects still use registered capabilities, immutable Operations,
idempotency and Receipts.

## Context

Every model decision can receive:

- relevant tool recommendations and available categories;
- the active Mega Project, when one exists;
- critical findings, current steps, recent decisions and latest checkpoint;
- ordinary recent turns, Memory Steward output, verified facts, Agent results and
  the neuron field.

This context is evidence. It does not replace the main AI's judgment.
