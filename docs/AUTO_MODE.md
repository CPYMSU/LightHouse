# LightHouse Auto Mode

LightHouse OS 0.9.0 adds a one-confirmation execution mode for long, multi-step
Runs.

## Behavior

With Auto Mode enabled, starting a natural-language task displays one scoped
Run authorization card. If the operator accepts it, LightHouse persists
`auto_confirm=true` on that Run and may confirm later governed operations without
showing another confirmation card.

```text
user task
  -> one Run authorization
  -> plan and Context Intelligence
  -> immutable Operation
  -> automatic confirmation for this Run
  -> Receipt
  -> continue from Receipt
  -> repeat until terminal/input state
```

The initial authorization is not global. It is bound to:

- one durable Run ID;
- one actor;
- one Workspace and its already-bound Targets;
- the task displayed in the authorization card;
- the Run's maximum step count.

The authorization ends when the Run:

- succeeds;
- fails;
- is cancelled;
- reaches its step limit; or
- pauses for new user input.

A new Run asks for a new authorization.

## Commands

Interactive terminal:

```text
/auto on
/auto off
/auto status
```

Direct command:

```text
lh auto on
lh auto off
lh auto status
```

Auto Mode is enabled by default after upgrading to 0.9.0, but the default only
means the terminal offers the scoped authorization card. Nothing executes until
the operator accepts that card.

## Manual confirmation

When Auto Mode is off—or when the operator declines the Run authorization—each
operation whose capability requires explicit confirmation continues to display
its exact frozen operation card.

```text
/auto off
```

## Preserved execution guarantees

Auto Mode does not bypass the Operation Kernel. Every action still has:

- a registered typed capability;
- a bound Target and grounded execution address;
- immutable arguments and an envelope hash;
- an idempotency key;
- a durable Operation state;
- an execution Receipt;
- verification before the main AI claims completion.

It changes confirmation frequency, not execution truth or tool authority.

## Multi-instance behavior

The Auto Mode preference belongs to the selected local instance configuration.
The authorization itself belongs only to the Run created on that instance.
Other instances do not inherit it.
