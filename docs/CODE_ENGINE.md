# LightHouse Code Engine

LightHouse now exposes one coding control plane with three cooperating engines:

```text
LightHouse Run / Memory / Agent Bus
          |
          +-- native CodeFoundry
          +-- Codex app-server v2 compatibility engine
          +-- optional lighthouse-code-kernel Rust PTY/sandbox sidecar
          |
          `-- evaluation and promotion gate
```

## Engine modes

`LIGHTHOUSE_CODE_ENGINE_MODE` accepts:

- `native`: CodeFoundry is authoritative.
- `codex`: Codex app-server v2 is authoritative.
- `hybrid`: Codex supplies a read-only engineering advisory; CodeFoundry executes.
- `shadow`: Codex runs read-only for comparison; the native result remains authoritative.
- `auto`: use Codex when installed, otherwise native CodeFoundry.

The Codex integration uses the documented stdio JSONL protocol. It initializes a
connection, starts/resumes/forks threads, starts and steers turns, streams items,
handles approvals, triggers review and compaction, and retains a redacted receipt
digest in the LightHouse AgentRun ledger.

## Terminal

```bash
lh code doctor
lh code run "fix the failing parser tests"
lh code interactive
```

Interactive commands:

```text
/plan /review /compact /permissions /agents /diff /test
/resume /fork /status /interrupt /exit
```

## Authority

Codex is an optional coding engine, not a replacement for LightHouse identity,
memory or audit. The selected route is persisted once per Run. Codex server
requests are surfaced as explicit approval state; `acceptForSession` maps to a
Run-scoped LightHouse Auto grant. Events are redacted before persistence.

## Native Rust kernel

`rust/lighthouse-code-kernel` implements an optional JSONL process service with:

- PTY-backed processes;
- incremental stdout/stderr notifications;
- stdin, resize and terminate controls;
- bounded output;
- timeout enforcement;
- read-only, workspace-write and full-access policy selection;
- fail-closed platform sandbox wrapping.

Codex already contains its own Rust execution stack. The LightHouse sidecar exists
for native CodeFoundry, local utilities and provider-independent fallback.

## Evaluation

Evaluation fixtures are JSON and compare the same task across named adapters.
The promotion gate rejects a candidate when it lowers verified completion,
increases validation failures, widens diffs beyond the configured ratio, or
completes without required evidence.
