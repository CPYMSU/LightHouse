# LightHouse Brain

LightHouse's planning and action loop is a native part of the product, not a
separately installed agent runtime.

```text
intent
  -> context
  -> plan
  -> exact capability
  -> immutable Operation
  -> confirmation policy
  -> deterministic executor
  -> durable Receipt
  -> observation and verification
```

The implementation retains some `agent_*` database and API names for 0.2
compatibility, but the product boundary is one `lh` command and one LightHouse
service.

## Durable state

Runs and steps are persisted in PostgreSQL. A disconnect does not restart the
reasoning process. The next invocation restores the run, the pending Operation
and its Receipt.

## Project context

The System surface loads bounded context from `AGENTS.md`,
`AGENTS.override.md`, `LIGHTHOUSE.md` and `.lighthouse/project.yaml`, plus Git
status and the repository file index.

## Execution boundary

The model never receives direct shell, filesystem or database authority. It may
only select an exact capability from the current atlas. LightHouse freezes an
Operation envelope, applies confirmation rules, executes through the selected
Data/System executor and returns the durable Receipt to the reasoning loop.

## Integrated terminal

`lh` opens the native terminal. Entering a project directory and running `lh`
automatically creates or reuses a confined local workspace. `lh "task"` runs a
single task. The legacy `lh agent` command remains only as a script compatibility
alias.
