# LightHouse OS 0.1 architecture

## Purpose

LightHouse extends the Warehouse OS super-terminal idea into a general operation
kernel. PostgreSQL data operations and Linux server operations use one identity,
one capability catalogue and one receipt protocol. They are separate execution
surfaces, not separate chat systems.

```text
terminal / future AI planner
             |
       Capability Atlas
             |
       Operation Kernel
       /              \
PostgreSQL Executor   Linux Executor
repository / SQL      local process / OpenSSH
```

## Invariants

1. A capability is the only public execution verb.
2. Exact terminal commands and future AI tools resolve to the same capability.
3. A workspace binds one data target and one system target.
4. `data`, `system` and `auto` choose the execution surface; they never add permission.
5. Target records store environment-variable names, not DSNs or private keys.
6. Read-shaped SQL is also forced into a PostgreSQL read-only transaction.
7. High-risk capabilities persist an immutable envelope before confirmation.
8. Confirmation executes that exact envelope once; it does not create a nested card.
9. Operation claiming is an atomic status transition in PostgreSQL.
10. A durable receipt, not the HTTP connection, is the source of truth.

## Capability layers

### Data surface

- typed business capabilities (next Warehouse adapter slice);
- generic resource operations (next schema-graph slice);
- `data schema`, `data query`, `data exec`.

### System surface

- typed service, journal and Git capabilities;
- structured file/process operations (next slice);
- raw `system exec` as the final explicit escape hatch.

Only the current surface needs to be loaded into an AI provider request. `auto`
mode is a planner/router: each operation still resolves to exactly one kernel and
one target.

## PostgreSQL control plane

The control database stores:

- `lh_targets` and `lh_workspaces`;
- immutable operation envelopes and request hashes;
- append-only operation events;
- immutable receipts;
- smart-index nodes and graph edges.

Business databases remain separate targets. A target names a server-side secret
environment variable (`dsn_env`) and never exposes its DSN to the terminal or AI.

## Linux execution

System targets use either:

- `local`: run through a configured local shell;
- `ssh`: run through the installed OpenSSH client with strict host-key checking
  and batch mode.

The first slice intentionally does not disable arbitrary shell. `system exec` is
an explicit high-risk capability and therefore requires confirmation. Typed
commands are preferred because they produce smaller, more stable operation
contracts.

## Confirmation model

0.1 implements explicit actor confirmation. The operation is first frozen with
its target, arguments and envelope hash, then `/confirm` atomically claims and
executes it. The schema reserves the same boundary for a WebAuthn/Passkey proof
without changing executors.

## Relationship with Warehouse OS

The reusable concepts are the Warehouse Command Set, capability atlas,
server-owned database routing, confirmation cards and receipt recovery. The new
repository does not copy the Warehouse monolith. Warehouse becomes the first
business adapter on top of this smaller kernel.

## Deliberate 0.1 limits

- one operator API credential; RBAC and device-key issuance come next;
- no model provider is embedded yet;
- no background job worker for very long commands;
- event endpoint replays current events; live tailing will use a worker/pubsub
  boundary in the next slice;
- PostgreSQL integration tests require a running database and are separate from
  the pure kernel unit tests.
