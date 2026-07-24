# LightHouse Agent Runtime 0.2

## Goal

Provide a coding-agent experience similar to modern terminal agents while
retaining LightHouse's stronger invariant:

> The model never executes a database, file, Git or Linux side effect directly.
> It can only request a registered capability; the Operation Kernel creates the
> immutable envelope, applies confirmation policy and records a Receipt.

## Runtime loop

```text
create durable agent run
  -> collect project context through system.project.context.v1
  -> ask the provider for exactly one structured decision
  -> validate exact capability and current kernel mode
  -> create an idempotent Operation
  -> execute direct reads or pause for confirmation
  -> append the complete Receipt as an observation
  -> repeat until verified final / user question / maximum steps
```

Agent state is stored in `lh_agent_runs`. Every transition, model decision,
tool dispatch, confirmation pause and observation is stored in
`lh_agent_steps`. A process restart therefore does not erase the reasoning
surface needed to resume the task.

## Model decision protocol

The model returns exactly one JSON object:

```json
{"kind":"tool","capability":"system.git.status.v1","arguments":{},"reason":"inspect"}
```

```json
{"kind":"final","message":"Tests pass and the diff is verified.","reason":"done"}
```

```json
{"kind":"ask","message":"Which service should be restarted?","reason":"ambiguous"}
```

Unknown capabilities, cross-kernel calls and malformed arguments fail closed
before execution.

## Project memory

`system.project.context.v1` returns:

- tracked and untracked project-file names;
- Git status;
- bounded contents of configured instruction files;
- the effective target working directory.

Default instruction candidates are:

- `AGENTS.md`
- `AGENTS.override.md`
- `LIGHTHOUSE.md`
- `.lighthouse/project.yaml`

This follows the mature project-instruction concept used by coding agents, but
the current slice scans the configured project root only. Hierarchical
root-to-working-directory instruction composition is planned separately.

## Coding capability pack

Read capabilities execute directly:

- `system.project.context.v1`
- `system.file.read.v1`
- `system.file.search.v1`
- `system.git.status.v1`
- `system.git.diff.v1`

Write or command capabilities freeze an Operation and require confirmation:

- `system.file.patch.v1`
- `system.shell.exec.v1`
- `system.test.run.v1`
- `system.git.commit.v1`
- `system.service.restart.v1`

`system.git.commit.v1` requires an explicit paths array. It cannot silently
stage the entire working tree.

## Local and SSH execution

The same `SystemExecutor` supports:

- `transport=local`
- `transport=ssh`

SSH is non-interactive (`BatchMode=yes`), uses strict host-key checking by
default and reads identity/known-hosts paths from server environment-variable
references. Target secrets do not enter the control-plane database.

Working directories and file operations are confined to target
`allowed_roots`. Output, time and result size are bounded and stored in the
Operation Receipt.

## Confirmation behavior

- `direct`: executes immediately.
- `explicit`: pauses unless the user confirms or starts the agent with `--yes`.
- `passkey`: always pauses; `--yes` cannot bypass it.

After confirmation, the exact frozen Operation executes. The agent then reads
the Receipt and continues; it does not create a second confirmation for the same
operation.

## Source strategy

The implementation reuses LightHouse's existing Operation Kernel and concepts
proven in `CPYMSU/warehouse`: shared capability registry, deterministic
execution, append-only events and receipt recovery. MSU's standardized
signal/event-bus concept informed the uniform agent-step stream.

OpenAI Codex's public `AGENTS.md` discovery model and coding-loop architecture
were reviewed as design references. No Codex or Claude Code source was copied
into LightHouse; the runtime is a native Python implementation around the
LightHouse capability and receipt contracts.
