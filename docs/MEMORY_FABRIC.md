# LightHouse Memory Fabric 0.7

## Purpose

Memory Fabric makes file, task, conversation and locator state durable across
runs, terminal restarts and conversations. It does not replace the filesystem.
Files remain the source of truth; PostgreSQL stores bounded searchable metadata,
relationships, history and successful execution coordinates.

```text
User message
  -> Conversation + active task
  -> Memory Resolver
  -> exact active subject / relevant files / recent locators
  -> LightHouse Brain
  -> Address Resolver
  -> immutable Operation
  -> Receipt
  -> Memory projection
```

## Persistent memory

- `lh_conversations` — one durable conversation per Workspace and actor;
- `lh_messages` — complete user and assistant messages;
- `lh_memory_tasks` — active/completed goals and their subject locator;
- `lh_locators` — canonical file, directory, URL and application addresses;
- `lh_files` — file metadata, hashes and bounded searchable text;
- `lh_file_revisions` — content hashes tied to successful runs and Operations;
- `lh_run_conversations` — the run-to-conversation bridge;
- `lh_memory_projections` — idempotent Receipt/message projection ledger.

## Automatic file index

The terminal performs an initial bounded scan of the System Target's explicit
`allowed_roots`. It skips common dependency, cache, VCS and macOS Library trees.
Text files up to 1 MB contribute bounded searchable text; larger or binary files
store metadata and hashes only. Successful file Operations refresh the relevant
entry immediately. `/reindex` performs an explicit refresh.

The index never expands authority. It only covers paths already allowed by the
System Target.

## Address grounding

The model may select a capability but does not own execution coordinates.
Before dispatch, `ExecutionAddressResolver` compares proposed `cwd` and `path`
values against:

1. the bound Workspace root;
2. the current task's active subject;
3. indexed files and directories;
4. recent canonical locators;
5. successful Receipt paths.

An absolute `cwd` that was not observed through those sources is rejected before
an Operation is created. A remembered file name such as `index.html` is resolved
to the canonical active file. New paths must be relative to the Workspace and use
typed capabilities.

## Typed directory creation

`mkdir` is not accepted through `system.shell.exec.v1`. Directory creation uses:

```text
system.directory.create.v1
```

The path is a safe relative path under the bound Workspace root and receives its
own frozen Operation and Receipt.

## Confirmation recovery

`confirm-deferred` claims the frozen Operation and immediately persists it as
`RUNNING`, then executes it in a worker. The terminal polls the durable Operation
until a Receipt exists. This separates:

```text
EXECUTE / WAIT FOR OPERATION RECEIPT
BRAIN / CONTINUE FROM RECEIPT
```

A terminal disconnect or HTTP timeout cannot erase the execution truth. The run
can be resumed from PostgreSQL.

## Conversation behavior

The terminal reuses the current conversation ID automatically. `/new` starts a
new conversation while retaining long-term indexed memory. Follow-up phrases such
as "continue", "the page from before", "this file" or "make it richer" are
resolved from the active task and locator before the Brain asks for a path.

A new unrelated task does not inherit the previous subject automatically. Subject
inheritance is limited to clearly referential follow-ups.

## UI contract

Timeline rows may summarize content. The final green result card and amber input
card are authoritative text and must never truncate a single long logical line.
Rich global `soft_wrap` is disabled; card text explicitly folds to the terminal
width.
