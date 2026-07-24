# LightHouse OS

LightHouse is one integrated PostgreSQL-first AI operating terminal.

It is not “LightHouse plus a separately installed Agent Runtime.” Planning,
context, action, observation, verification, memory and recovery are native
LightHouse capabilities, and every side effect still passes through the governed
Operation Kernel.

```text
user intent
  -> LightHouse Brain
  -> project/data context
  -> exact capability
  -> immutable Operation
  -> Data or System executor
  -> durable Receipt
  -> verification or next action
```

## Install on macOS with one command

Open Terminal and run:

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh | bash
```

The installer installs Homebrew when necessary, Python 3.12, PostgreSQL 16 and
Git; downloads the complete LightHouse package; creates a private local
PostgreSQL control plane; asks for the model endpoint, model name and hidden API
key; stores credentials in macOS Keychain; installs a `launchd` background
service; installs the single `lh` command; and runs migrations and health checks.

No API key is written to the repository or `~/.lighthouse/config.json`.

After installation:

```bash
cd /path/to/your/project
lh
```

## Swiss Super Terminal

The native terminal follows the visual discipline of the Warehouse OS 2.0 Super
Terminal: paper/ink/red contrast, strict grids, compact uppercase labels, visible
kernel state and receipt-backed execution steps.

```text
LH  /  LIGHTHOUSE OS                       FOLIO 0.4 · AI OPERATING TERMINAL
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KERNEL        WORKSPACE        BRAIN          CONTROL
SYSTEM        project-local    READY          LOCAL / SECURE
PROJECT  /Users/you/project
────────────────────────────────────────────────────────────────────────────

LH / SYSTEM / project  ›
```

During a run, LightHouse exposes the governed lifecycle instead of hiding it:

```text
01  PLAN       understand the requested goal
02  CONTEXT    index project instructions and source state
03  THINK      select one exact authorized capability
04  EXECUTE    create and dispatch an immutable Operation
05  CONFIRM    show a frozen action card when required
06  VERIFY     read the durable Receipt and test the outcome
08  COMPLETE   return the verified final answer
```

Interactive controls include command history, auto-suggestion, Tab completion,
a bottom status toolbar and local commands that never auto-run:

```text
/help
/status
/capabilities [query]
/mode auto|system|data
/init [path]
/doctor
/clear
/exit
```

Prefix an exact compatibility command with `!`, for example:

```text
! capabilities git
```

A single task can also be sent directly:

```bash
lh "inspect the failing tests, fix the root cause and verify the result"
```

## Built-in capabilities

### System surface

- local and OpenSSH Linux targets;
- project instruction and context loading;
- bounded file read and search;
- unified diff application;
- shell and configured test execution;
- Git status, diff and explicit-path commit;
- systemd status/restart and journal reads.

### Data surface

- PostgreSQL schema inspection;
- server-enforced read-only queries;
- transactional mutations with frozen confirmation and durable Receipts.

### LightHouse Brain

The native reasoning loop loads project and operation context, selects one exact
authorized capability, submits an idempotent Operation, pauses for confirmation
when required, observes the durable Receipt, verifies the result and resumes from
PostgreSQL after a disconnect. The model never receives raw filesystem, shell or
database authority.

## Useful commands

```bash
lh                         # Swiss interactive terminal
lh "task"                  # one natural-language task
lh init [PATH]             # bind a project directory
lh login                   # replace the model key in macOS Keychain
lh doctor                  # verify installation
lh capabilities            # inspect the capability atlas
lh run ...                 # exact governed operation
lh operation UUID
lh receipt UUID
```

The older `lh agent ...` form remains as a compatibility alias for scripts, but
there is no separately installed Agent service.

## Project instructions

LightHouse reads bounded project guidance from:

- `AGENTS.md`
- `AGENTS.override.md`
- `LIGHTHOUSE.md`
- `.lighthouse/project.yaml`

## Security model

- The HTTP service binds to `127.0.0.1` by default.
- API keys are stored in macOS Keychain.
- Database and SSH secrets are referenced by server environment names.
- High-risk writes freeze an exact Operation before confirmation.
- `--yes` cannot bypass Passkey policies.
- Receipts, not client timeouts, are the source of execution truth.
- Local project targets are confined to explicit allowed roots.
- SSH host-key checking remains strict by default.

## Remove LightHouse

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/uninstall-macos.sh | bash
```

The uninstaller preserves PostgreSQL data rather than deleting it automatically.

See `docs/ARCHITECTURE.md`, `docs/AGENT_RUNTIME.md` and
`docs/TERMINAL_UI.md` for internal contracts.
