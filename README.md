# LightHouse OS

LightHouse OS is a PostgreSQL-first super-terminal kernel for operating two
worlds through one governed protocol:

- **Data surface:** PostgreSQL schema inspection, read queries and transactional
  `data exec` mutations.
- **System surface:** local or SSH Linux shell, systemd, journal and Git commands.

Every action is an immutable **Operation** with append-only events and a durable
**Receipt**. Exact CLI commands and future AI function calls share the same
capability registry.

## Current 0.1 slice

This first implementation includes:

- FastAPI HTTP gateway for terminals on any computer;
- PostgreSQL control-plane schema and migrations;
- data/system/auto routing modes;
- versioned capability atlas with exact and lexical search;
- PostgreSQL data executor using server-held DSN environment variables;
- local and OpenSSH Linux executor;
- one-step explicit confirmation for high-risk operations;
- idempotent, immutable operation receipts;
- CLI context that stores no API key or database credential;
- initial smart-index node and edge tables;
- unit tests and GitHub Actions CI.

The AI provider/planner is intentionally not hard-coded in this slice. It will
consume the same `/v1/capabilities` and `/v1/operations` contracts without
changing the execution boundary.

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

In a second terminal:

```bash
export LIGHTHOUSE_URL='http://127.0.0.1:8787'
export LIGHTHOUSE_API_KEY='replace-with-a-long-random-token'

lh migrate
lh capabilities
```

## Register targets

Secrets remain on the API server. The target records contain only environment
variable names and non-secret routing metadata.

```bash
export WAREHOUSE_DATABASE_URL='postgresql://warehouse:secret@db.internal/warehouse'
export WAREHOUSE_SSH_KEY='/secure/path/warehouse_ed25519'

lh target-add warehouse-db --kind data \
  --config-json '{"dsn_env":"WAREHOUSE_DATABASE_URL","read_only":false}'

lh target-add warehouse-server --kind system \
  --config-json '{"transport":"ssh","host":"server.example.com","user":"warehouse","identity_file_env":"WAREHOUSE_SSH_KEY","default_cwd":"/opt/warehouse"}'

lh targets
```

Use returned target IDs to create a workspace:

```bash
lh workspace-add warehouse-prod \
  --data-target DATA_TARGET_UUID \
  --system-target SYSTEM_TARGET_UUID

lh workspaces
lh configure --workspace WORKSPACE_UUID --mode auto --actor adsin
```

## Execute PostgreSQL operations

Read operations execute immediately:

```bash
lh run data.sql.query.v1 \
  --args-json '{"sql":"select now() as server_time"}'
```

Mutations are persisted first and require one confirmation. `--confirm` confirms
that exact frozen operation immediately:

```bash
lh run data.sql.exec.v1 --confirm \
  --idempotency-key create-example-1 \
  --args-json '{"sql":"insert into examples(name) values (%s) returning id","params":["LightHouse"]}'
```

## Execute Linux server commands

```bash
lh run system.journal.read.v1 \
  --args-json '{"service":"warehouse-api","lines":200}'

lh run system.service.restart.v1 --confirm \
  --args-json '{"service":"warehouse-api"}'

lh run system.shell.exec.v1 --confirm \
  --args-json '{"command":"python3 -m pytest -q","cwd":"/opt/warehouse"}'
```

## Recover after a disconnect

```bash
lh events OPERATION_UUID
lh receipt OPERATION_UUID
```

A client timeout never proves failure. The receipt is the source of truth, and
an idempotency key prevents a caller from repeating a completed side effect.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the boundary design.
