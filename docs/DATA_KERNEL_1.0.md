# LightHouse Data Kernel 1.0

## Position

PostgreSQL is the structured world-state layer of LightHouse. The AI does not
receive unrestricted database authority. It receives an authorized capability
surface and every read, mutation, catalog sync and policy change is represented
as an immutable Operation with a durable Receipt.

```text
User intent
  -> LightHouse Brain
  -> Semantic command or Resource capability
  -> Data Target alias router
  -> PostgreSQL executor
  -> Receipt
```

## Two PostgreSQL planes

### LightHouse Core Database

Stores operating-system state, Data Target bindings, schema graph metadata,
resource policies, semantic commands, Operations, Receipts and run memory.

### Connected Data Databases

Store domain worlds such as Warehouse ERP, inventory, finance, engineering and
customer systems. Credentials remain in server environment variables; target
records contain only `dsn_env` references and policy.

## Federation

A workspace keeps its original `data_target_id` as the compatibility default and
may bind additional Data Targets through stable aliases.

```bash
lh run data.target.bind.v1 --mode data --confirm \
  --args-json '{"target_id":"FINANCE_TARGET_UUID","alias":"finance"}'
```

Operations select a world with `target_alias`. If omitted, the default binding is
used.

## Catalog and schema graph

```bash
lh run data.catalog.sync.v1 --mode data \
  --args-json '{"target_alias":"erp"}'
```

The sync reads authorized PostgreSQL metadata and persists:

- database, schema, table and column nodes;
- containment and foreign-key reference edges;
- primary keys;
- one default read-only Resource for every discovered table.

A subsequent sync updates structural metadata while preserving explicit
write-policy and semantic-command configuration.

## Command layers

### 1. Semantic commands

Semantic commands are declarative mappings to a known Resource. They cannot
contain arbitrary SQL templates.

```bash
lh run data.semantic.register.v1 --mode data --confirm --args-json '{
  "target_alias":"erp",
  "command":"data.purchase.pending",
  "resource":"public.purchase_requests",
  "action":"search",
  "definition":{
    "fixed_filters":{"status":"pending"},
    "param_filters":{"department":"department"},
    "order_by":["-created_at"],
    "limit":100
  }
}'
```

Execute it with:

```bash
lh run data.semantic.query.v1 --mode data --args-json '{
  "target_alias":"erp",
  "command":"data.purchase.pending",
  "params":{"department":"research"}
}'
```

### 2. Resource commands

```text
data.resource.list.v1
data.resource.show.v1
data.resource.search.v1
data.resource.update.v1
```

Reads use declared columns, filters, ordering and maximum row limits. Updates
require an explicit confirmation and only accept columns enabled by
`data.resource.policy.v1`. Catalog sync never enables writes automatically.

Supported filter suffixes are `__eq`, `__in`, `__contains`, `__gte`, `__lte`,
`__gt`, `__lt` and `__isnull`.

### 3. Raw SQL

`data.sql.query.v1` and `data.sql.exec.v1` remain expert fallbacks. Each Data
Target can independently disable raw reads or mutations with `raw_sql_query` and
`raw_sql_exec`. A read-only target always disables raw mutations.

## Data Target policy

```json
{
  "dsn_env":"WAREHOUSE_DATABASE_URL",
  "read_only":false,
  "allowed_schemas":["public","erp"],
  "excluded_schemas":["pg_catalog","information_schema"],
  "raw_sql_query":true,
  "raw_sql_exec":false,
  "max_rows":500
}
```

## Brain context

LightHouse Brain receives a bounded `data_worlds` section containing workspace
aliases, resource names, primary keys, readable/writable columns and registered
semantic commands. It is instructed to prefer Semantic → Resource → SQL and may
not invent schemas, tables, columns or commands.

## Persistent core tables

- `lh_workspace_data_targets`
- `lh_schema_nodes`
- `lh_schema_edges`
- `lh_data_resources`
- `lh_semantic_commands`

These tables describe the world; business records remain in their connected
PostgreSQL databases.
