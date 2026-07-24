# LightHouse OS

LightHouse is one integrated PostgreSQL-first AI operating terminal with three
governed execution surfaces:

- **Data Kernel** — PostgreSQL and structured business data;
- **System Kernel** — files, code, Git, tests, local Bash/PowerShell and OpenSSH Linux servers;
- **Desktop Kernel** — semantic macOS and Windows application, browser and file launching.

Planning, context, action, observation, verification, memory and recovery are
native capabilities. Every side effect passes through the Operation Kernel and
produces a durable Receipt.

```text
user intent
  -> Memory Fabric + LightHouse Brain
  -> capability atlas
  -> server-grounded cwd/path
  -> immutable Operation
  -> Data / System / Desktop executor
  -> durable Receipt
  -> memory projection and verification
```

## Install on macOS

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh | bash
```

The installer prepares Python 3.12, PostgreSQL 16 and Git, stores credentials in
macOS Keychain, installs a `launchd` service and the `lh` command, then migrates
and checks the local control plane.

## Install on Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

The Windows installer uses WinGet when dependencies are missing, protects
credentials with current-user DPAPI, adds `lh.cmd` to the user PATH and registers
a current-user `LightHouse` Scheduled Task. No model API key is written to the
repository or `config.json` on either platform.

## Swiss Super Terminal

Open a project directory and run:

```text
cd /path/to/project       # macOS
cd C:\path\to\project    # Windows PowerShell
lh
```

LightHouse binds confined System/Desktop Targets, initializes the authorized
file index and selects `AUTO`.

```text
lh> create a Swiss-style dashboard.html and open it in the default browser
```

The run remains visible:

```text
01  PLAN       understand the goal
02  CONTEXT    load project, conversation and file memory
03  ADDRESS    ground cwd/path against indexed locators and Receipts
03  THINK      select one exact capability
04  EXECUTE    dispatch an immutable Operation
05  CONFIRM    show the frozen action card
06  VERIFY     read the durable Receipt
08  COMPLETE   render the complete verified answer
```

## Memory Fabric 0.7

The LightHouse Core PostgreSQL database now preserves:

- complete user and assistant messages;
- active/completed tasks and their subject file or URL;
- canonical file, directory and URL locators;
- file paths, types, sizes, hashes and bounded searchable text;
- file revisions tied to successful Runs and Operations;
- recent successful Receipt paths.

The filesystem remains the source of truth. The index cannot expand authority
beyond each System Target's explicit `allowed_roots`.

Follow-ups such as:

```text
make it richer
continue the page from before
open that file again
```

resolve the active subject before asking for another path. `/new` starts a new
conversation while retaining long-term memory. `/reindex` refreshes authorized
file and directory locators.

### Address grounding

The model may choose a capability, but it does not own execution coordinates.
Before an Operation is created, the server compares every proposed `cwd/path`
with:

1. the bound Workspace root;
2. the current task's active subject;
3. indexed files and real directories;
4. recent canonical locators;
5. successful Receipt paths.

An invented or previously unobserved absolute `cwd` is rejected. A referential
request for `index.html` resolves to the canonical active file. New paths must be
relative to the Workspace and use typed capabilities. Unrelated new tasks do not
silently inherit the previous subject.

### Typed directory creation

Directory creation uses:

```text
system.directory.create.v1
```

`mkdir` through arbitrary Bash is rejected; the Brain is also instructed not to
use PowerShell `New-Item` for this purpose. The relative path is frozen, grounded
and Receipt-backed.

## Recoverable confirmation

Confirmation no longer holds one HTTP request open through both command execution
and the next model call. The Operation is first persisted as `RUNNING`, then the
terminal polls PostgreSQL-backed state:

```text
EXECUTE / WAIT FOR OPERATION RECEIPT
BRAIN / CONTINUE FROM RECEIPT
```

A terminal disconnect or client timeout cannot erase execution truth. The same
Run and Operation can be resumed from PostgreSQL.

## Terminal text contract

Timeline rows may be summarized. The green final card and amber input card are
authoritative and never truncate a long single logical line; their text folds to
the terminal width.

## Desktop Kernel

The Desktop Kernel uses semantic operating-system services rather than mouse
coordinates:

- macOS: `/usr/bin/open` and Launch Services;
- Windows: PowerShell `Start-Process` and the Windows shell.

Capabilities:

- `desktop.browser.open_url.v1`
- `desktop.file.open.v1`
- `desktop.app.open.v1`

Browser page interaction remains a future Playwright/CDP adapter behind the same
Capability → Operation → Receipt contract.

## Built-in surfaces

### System

- local macOS/Linux Bash and Windows PowerShell;
- OpenSSH Linux targets;
- project context, bounded file read/search and atomic UTF-8 writes;
- typed directory creation;
- patch, test, Git and service operations.

### Data

- PostgreSQL federation through Data Target aliases;
- schema graph and Resource Catalog synchronization;
- semantic and typed Resource queries;
- expert SQL with per-target policy;
- transactional mutations with frozen confirmation.

### Brain

The native loop loads project, conversation, task, locator, file, data-world and
Operation context; grounds execution addresses; selects an exact capability;
observes Receipts; verifies outcomes; and resumes after disconnects. The model
never receives raw filesystem, shell, database, desktop or address authority.

## Useful commands

```text
lh                              # Swiss terminal
lh "task"                       # one natural-language task
lh init [PATH]                  # bind project targets and index files
lh login                        # update the model key in the native secret store
lh doctor                       # verify control plane, conversation and memory
lh capabilities                 # inspect active capabilities
lh mode auto|data|system|desktop
```

Interactive controls:

```text
/help
/new
/reindex
/status
/capabilities [query]
/mode auto|system|data|desktop
/init [path]
/doctor
/receipt OPERATION_ID
/clear
/exit
```

## Security model

- The service binds to `127.0.0.1` by default.
- Secrets use macOS Keychain or current-user Windows DPAPI.
- High-risk writes freeze an exact Operation before confirmation.
- Receipts, not client timeouts, are execution truth.
- System/Desktop Targets and the file index remain inside allowed roots.
- Unobserved execution addresses are rejected before dispatch.
- Desktop URL schemes/applications are allow-listed.
- SSH host-key checking remains strict by default.

## Upgrade

### macOS

```bash
git -C ~/.lighthouse/app fetch origin main
git -C ~/.lighthouse/app reset --hard origin/main
~/.lighthouse/venv/bin/pip install --upgrade ~/.lighthouse/app
launchctl kickstart -k gui/$(id -u)/com.cpym.su.lighthouse
lh migrate
```

### Windows

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

See `docs/ARCHITECTURE.md`, `docs/AGENT_RUNTIME.md`, `docs/TERMINAL_UI.md`,
`docs/DESKTOP_KERNEL.md`, `docs/DATA_KERNEL_1.0.md` and
`docs/MEMORY_FABRIC.md`.
