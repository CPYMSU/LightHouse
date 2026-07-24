# LightHouse OS

LightHouse OS is a PostgreSQL-first governed AI super terminal. One durable
Operation Kernel controls two execution surfaces:

- **Data:** PostgreSQL schema inspection, read-only queries and transactional
  mutations.
- **System:** local or OpenSSH Linux files, shell, systemd, journal, Git, patch
  and test commands.

Every side effect is first frozen into an immutable Operation envelope. Events
and the final Receipt remain the source of truth after a timeout or disconnect.

## 0.2 agent-runtime slice

The current branch adds a Codex/Claude-Code-style coding loop without bypassing
the LightHouse execution boundary:

```text
task
  -> load project context and AGENTS.md/LIGHTHOUSE.md
  -> model selects one exact capability
  -> Operation Kernel validates and dispatches
  -> Receipt becomes the next observation
  -> inspect / patch / test / diff / verify
  -> final answer
```

Implemented capabilities include:

- PostgreSQL `data schema`, `data query` and transactional `data exec`;
- local/OpenSSH Linux execution;
- project file index and project-instruction loading;
- bounded file read and search;
- unified diff application through `git apply`;
- Git status, diff and explicit-path commit;
- configurable test execution;
- systemd status/restart and journal reads;
- durable agent runs and append-only agent steps;
- confirmation pause/resume and optional `--yes` auto-confirmation for
  **explicit** operations only. Passkey operations are never auto-confirmed.

## Start locally

```bash
docker compose up -d
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'

export LIGHTHOUSE_DATABASE_URL='postgresql://lighthouse:lighthouse@127.0.0.1:5432/lighthouse'
export LIGHTHOUSE_API_KEY='replace-with-a-long-random-token'

lighthouse-api
```

In another terminal:

```bash
export LIGHTHOUSE_URL='http://127.0.0.1:8787'
export LIGHTHOUSE_API_KEY='replace-with-a-long-random-token'

lh migrate
lh capabilities
```

## Configure an AI model

The runtime uses a Chat-Completions-compatible model endpoint. Model credentials
stay in the API server environment and are never written into PostgreSQL.

```bash
export LIGHTHOUSE_MODEL_BASE_URL='https://api.example.com/v1'
export LIGHTHOUSE_MODEL_API_KEY='model-secret'
export LIGHTHOUSE_MODEL='your-model-name'
```

`LIGHTHOUSE_MODEL_JSON_MODE=0` disables the provider `response_format` field for
providers that do not support JSON mode.

## Register targets

Targets contain only secret references and non-secret routing metadata.

```bash
export WAREHOUSE_DATABASE_URL='postgresql://warehouse:secret@db.internal/warehouse'
export WAREHOUSE_SSH_KEY='/secure/path/warehouse_ed25519'
export WAREHOUSE_KNOWN_HOSTS='/secure/path/known_hosts'

lh target-add warehouse-db --kind data \
  --config-json '{"dsn_env":"WAREHOUSE_DATABASE_URL","read_only":false}'

lh target-add warehouse-server --kind system \
  --config-json '{
    "transport":"ssh",
    "host":"server.example.com",
    "user":"warehouse",
    "identity_file_env":"WAREHOUSE_SSH_KEY",
    "known_hosts_env":"WAREHOUSE_KNOWN_HOSTS",
    "strict_host_key":true,
    "default_cwd":"/opt/warehouse",
    "allowed_roots":["/opt/warehouse"],
    "test_command":"python3 -m pytest -q"
  }'
```

Create and select a workspace:

```bash
lh workspace-add warehouse-prod \
  --data-target DATA_TARGET_UUID \
  --system-target SYSTEM_TARGET_UUID

lh use auto WORKSPACE_UUID --actor adsin
```

## Exact operations

Read operations execute immediately:

```bash
lh run data.sql.query.v1 \
  --args-json '{"sql":"select now() as server_time"}'

lh run system.project.context.v1
lh run system.git.diff.v1
```

Writes create one frozen confirmation action:

```bash
lh run data.sql.exec.v1 --confirm \
  --idempotency-key create-example-1 \
  --args-json '{"sql":"insert into examples(name) values (%s) returning id","params":["LightHouse"]}'

lh run system.file.patch.v1 --confirm \
  --args-json '{"patch":"<unified diff>"}'
```

## Coding agent

Place project-specific rules in `AGENTS.md`, `AGENTS.override.md`,
`LIGHTHOUSE.md`, or `.lighthouse/project.yaml` inside the configured project
root.

Start a governed agent run:

```bash
lh agent "inspect the failing tests, fix the root cause, run tests and show the diff"
```

When a write needs confirmation, the run pauses with a pending Operation:

```bash
lh confirm OPERATION_UUID
lh agent-resume AGENT_RUN_UUID
```

For an intentionally unattended development workspace, `--yes` lets the run
confirm ordinary `explicit` operations:

```bash
lh agent --yes "fix the tests and verify the result"
```

This option does not bypass Passkey policies.

## Recover state

```bash
lh operation OPERATION_UUID
lh events OPERATION_UUID
lh receipt OPERATION_UUID

lh agent-show AGENT_RUN_UUID
lh agent-resume AGENT_RUN_UUID
```

A client timeout never proves failure. Query the durable Receipt or agent run.

## Current boundary

This is a foundation release, not a production multi-user deployment. The
gateway currently uses one operator credential. Device-bound identities, RBAC,
WebAuthn/Passkey verification, streaming long-running workers, nested
directory-specific `AGENTS.md` discovery and the Warehouse business adapter are
follow-up slices.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and
[docs/AGENT_RUNTIME.md](docs/AGENT_RUNTIME.md).
