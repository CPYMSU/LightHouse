# LightHouse Lazy Auto Mode

LightHouse OS 1.2 keeps ordinary conversation frictionless and asks for authority
only when a governed side effect actually needs it.

## Behavior

Starting a natural-language Run does not display an Auto Mode authorization card.
The main AI may converse, inspect context, search tools, read files, dispatch
read-only Agents and perform research without requesting execution authority.

When the first Operation that requires explicit confirmation is frozen, the terminal
shows the exact action and offers:

```text
[once] Allow once
[auto] Auto-approve this Run
[deny] Deny
```

```text
user task
  -> Context Intelligence and optional Agents
  -> main AI selects a governed side effect
  -> immutable Operation is frozen
  -> action-time permission card
       -> once: confirm this Operation only
       -> auto: confirm this Operation and grant a compatible Run scope
       -> deny: keep the Operation pending
  -> Receipt
  -> continue from Receipt
```

## Scope

A Run-scoped Auto grant is bound to:

- one durable Run ID and actor;
- one Workspace;
- one exact target and kernel;
- the capability class first authorized;
- the target's existing allowed roots;
- terminal/input state.

A later capability or target outside that scope asks again. Auto Mode cannot expand
System or Desktop roots, bypass address validation, or make a high-risk capability
available to an unauthorized Agent.

The grant ends when the Run:

- succeeds or completes with a warning;
- fails or is cancelled;
- reaches its step limit;
- pauses for new user input; or
- requests authority outside the established scope.

## Commands

```text
/auto on
/auto off
/auto status

lh auto on
lh auto off
lh auto status
```

`/auto on` means that the action-time card offers **Auto-approve this Run**. It does
not pre-authorize every new Run. `/auto off` keeps exact one-time confirmation only.

## Specialist Agents

Read-only specialist work does not require Auto Mode. An Agent may request a side
effect only when:

1. the capability belongs to that Agent's registered tool set;
2. the parent Run has a compatible Auto scope;
3. the path remains inside the target's allowed roots; and
4. Massive Build writes also have a valid non-overlapping Write Lease.

Otherwise the Agent returns a permission-needed finding to the main AI instead of
writing around the boundary.

## Preserved execution guarantees

Lazy Auto changes when permission is requested, not execution truth. Every action
still has a typed capability, grounded Target and address, immutable arguments,
idempotency key, durable state and Receipt.

## Multi-instance behavior

The preference belongs to one local instance configuration. A grant belongs only to
the Run that received it. Other instances and Runs do not inherit the authority.
