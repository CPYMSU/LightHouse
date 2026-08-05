# Warehouse OS 2.1 Federation

LightHouse implements the device side of `warehouse-lighthouse-federation/v1`.
The device opens an outbound WebSocket connection to Warehouse over WSS/443; no
inbound desktop port, SSH credential, database password, or filesystem address is
exposed to Warehouse.

## Pairing

Create a ten-minute pairing challenge in Warehouse, then run:

```bash
lh warehouse pair https://warehouse.example.com 'whp_...' \
  --workspace '<local-workspace-id>' \
  --label 'Mac mini'
```

The one-time device token is written to macOS Keychain or Windows DPAPI. The
LightHouse JSON configuration contains only the Warehouse origin, device ID,
label, and optional local workspace mapping. A running LightHouse service notices
the new configuration and connects without a restart.

```bash
lh warehouse status
lh warehouse disconnect
```

## v1 authority boundary

Warehouse sends a natural-language goal and this immutable policy:

```json
{"mode":"read_only","allow_local_write":false}
```

The policy is persisted before the local Agent Run starts. Every Operation created
by that Run is bound through its durable `agent:<run-id>:...` idempotency key. The
Operation Kernel resolves the Run policy before creating an Operation and rejects
all capabilities whose catalogue metadata declares `writes=true`.

This is a kernel boundary, not a model prompt. Remote approval messages are rejected
in v1. Local files, screenshots, tool arguments, raw observations, model context,
and full Receipts are not uploaded. Warehouse receives only allowlisted Run events,
a bounded final projection, and a SHA-256 Receipt digest.

## Delivery semantics

Both inbound and outbound messages use UUID idempotency keys. LightHouse persists:

- claimed inbound messages;
- the Warehouse Run to local Run mapping;
- the read-only policy;
- last projected Agent sequence;
- a durable outbound outbox;
- peer acknowledgements.

Unacknowledged messages are replayed after reconnect. Deterministic UUIDv5 IDs are
used for Run events, Receipt projections, and terminal completion messages so a
network interruption cannot duplicate a state transition.
