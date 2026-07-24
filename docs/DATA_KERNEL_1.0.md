# LightHouse Data Kernel 1.0

## Position

PostgreSQL is the structured world-state layer of LightHouse.

LightHouse does not expose PostgreSQL as a raw database console by default.
The Data Kernel converts intent into governed operations:

```
User intent
  -> LightHouse Brain
  -> Capability Atlas
  -> Data Kernel
  -> PostgreSQL Adapter
  -> Operation
  -> Receipt
```

## Two PostgreSQL planes

### LightHouse Core Database

Stores operating-system state:

- workspaces
- targets
- capabilities
- operations
- receipts
- agent runs
- indexes
- policies
- memories

### Connected Data Databases

Store domain worlds:

- Warehouse ERP
- inventory
- finance
- engineering systems
- customer data

A workspace can connect these worlds through Data Targets.

## Three command layers

### Semantic commands

Preferred path:

```
data.purchase.search
inventory.device.status
finance.balance.summary
```

The model selects business capabilities, not SQL.

### Resource commands

Generic structured access:

```
resource list
resource show
resource update
```

Schema metadata defines fields, relations and policies.

### SQL commands

Last-resort expert capability:

```
data.sql.query
 data.sql.exec
```

Mutations remain Operation controlled and receipt backed.

## Memory model

PostgreSQL stores:

1. Structured memory
2. Operation history
3. Schema graph metadata

Semantic retrieval can combine PostgreSQL full text, graph edges and future vector adapters.

## Design rule

The AI never receives unrestricted database authority. It receives an authorized capability surface and executes through Data Kernel policies.
