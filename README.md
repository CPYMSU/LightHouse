# LightHouse OS

LightHouse is one integrated PostgreSQL-first AI operating terminal with three
governed execution surfaces:

- **Data Kernel** — PostgreSQL and structured business data;
- **System Kernel** — files, code, Git, tests, local Bash/PowerShell and OpenSSH Linux servers;
- **Desktop Kernel** — semantic macOS and Windows application, browser and file launching.

Planning, context, action, observation, verification, memory and recovery are
native LightHouse capabilities. Every side effect still passes through the
Operation Kernel and produces a durable Receipt.

```text
user intent
  -> LightHouse Brain
  -> capability atlas
  -> immutable Operation
  -> Data / System / Desktop executor
  -> durable Receipt
  -> verification or next action
```

## Install on macOS with one command

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh | bash
```

The macOS installer prepares Python 3.12, PostgreSQL 16 and Git, downloads the
complete LightHouse package, stores credentials in macOS Keychain, installs a
`launchd` background service and the single `lh` command, then runs migrations
and health checks.

## Install on Windows PowerShell with one command

Open Windows Terminal or PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

The Windows installer:

- installs Python 3.12, PostgreSQL 16 and Git through WinGet when missing;
- creates the private local `lighthouse` PostgreSQL role and database;
- installs LightHouse into `%USERPROFILE%\.lighthouse`;
- protects control and model credentials with current-user Windows DPAPI;
- adds `%USERPROFILE%\.lighthouse\bin\lh.cmd` to the user PATH;
- registers a current-user `LightHouse` Scheduled Task at logon;
- runs migrations, health checks and `lh doctor`.

A fresh PostgreSQL installation is unattended. When an existing PostgreSQL
installation has no LightHouse configuration, the installer asks for its
`postgres` password. Model configuration can be supplied non-interactively with:

```powershell
$env:LIGHTHOUSE_MODEL_BASE_URL = "https://your-model-gateway.example/v1"
$env:LIGHTHOUSE_MODEL = "lighthouse-default"
$env:LIGHTHOUSE_MODEL_API_KEY = "your-lighthouse-token"
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

No model API key is written to the repository or `config.json` on either
platform.

## Swiss Super Terminal

Open a project directory and run:

```text
cd /path/to/project       # macOS
cd C:\path\to\project    # Windows PowerShell
lh
```

LightHouse automatically binds a confined local System Target and Desktop Target
and selects `AUTO`, so one goal can cross both surfaces:

```text
lh> create a Swiss-style dashboard.html and open it in the default browser
```

The governed chain is:

```text
System Kernel: inspect project -> create/apply HTML change -> Receipt
Desktop Kernel: resolve dashboard.html inside allowed root -> OS semantic open -> Receipt
LightHouse Brain: verify both receipts -> final response
```

The Warehouse-inspired paper/ink/red interface keeps the active kernel profile,
workspace, steps, confirmation cards and Receipts visible. During a run:

```text
01  PLAN       understand the requested goal
02  CONTEXT    index project instructions and source state
03  THINK      select one exact authorized capability
04  EXECUTE    create and dispatch an immutable Operation
05  CONFIRM    show a frozen action card when required
06  VERIFY     read the durable Receipt and test the outcome
08  COMPLETE   return the verified final answer
```

A single task can also be sent directly:

```text
lh "create an HTML dashboard and open it in the default browser"
```

## Desktop Kernel

The Desktop Kernel uses semantic operating-system launch services rather than
brittle mouse coordinates:

- macOS: `/usr/bin/open` and Launch Services;
- Windows: PowerShell `Start-Process` and the Windows shell.

It exposes exact capabilities:

- `desktop.browser.open_url.v1`
- `desktop.file.open.v1`
- `desktop.app.open.v1`

Targets explicitly constrain project/file roots, URL schemes and applications.
Opening an HTTP/HTTPS URL or a confined project file is a low-risk direct
capability. Launching an allow-listed application creates an explicit
confirmation Operation.

Browser page interaction is intentionally not simulated with mouse coordinates.
A future Playwright/CDP adapter can add semantic DOM navigation, form filling and
downloads behind the same Capability → Operation → Receipt contract.

## Built-in capabilities

### System surface

- local macOS/Linux Bash and Windows PowerShell targets;
- OpenSSH Linux targets from macOS, Linux or Windows hosts;
- project instruction and context loading;
- bounded file read and search;
- unified diff application;
- shell and configured test execution;
- Git status, diff and explicit-path commit;
- systemd status/restart and journal reads on Linux;
- Windows Service status/restart and filtered Event Log reads on Windows.

### Data surface

- PostgreSQL schema inspection;
- server-enforced read-only queries;
- transactional mutations with frozen confirmation and durable Receipts.

### LightHouse Brain

The native reasoning loop loads project and operation context, selects one exact
authorized capability, submits an idempotent Operation, pauses for confirmation
when required, observes the durable Receipt, verifies the result and resumes from
PostgreSQL after a disconnect. The model never receives raw filesystem, shell,
database or desktop authority.

## Useful commands

```text
lh                              # Swiss interactive terminal
lh "task"                       # one natural-language task
lh init [PATH]                  # bind System + Desktop targets
lh login                        # replace the model key in the native secret store
lh doctor                       # verify installation
lh capabilities                 # inspect all active capabilities
lh capabilities --kernel desktop
lh mode auto|data|system|desktop
```

Interactive controls include:

```text
/help
/status
/capabilities [query]
/mode auto|system|data|desktop
/init [path]
/doctor
/receipt OPERATION_ID
/clear
/exit
```

## Project instructions

LightHouse reads bounded project guidance from:

- `AGENTS.md`
- `AGENTS.override.md`
- `LIGHTHOUSE.md`
- `.lighthouse/project.yaml`

## Security model

- The HTTP service binds to `127.0.0.1` by default.
- Secrets are stored in macOS Keychain or current-user Windows DPAPI.
- High-risk writes freeze an exact Operation before confirmation.
- Receipts, not client timeouts, are the source of execution truth.
- Local System and Desktop targets are confined to explicit allowed roots.
- Desktop URL schemes and applications are allow-listed per target.
- SSH host-key checking remains strict by default.
- Windows background execution uses a current-user Scheduled Task with limited privileges.

## Upgrade an existing installation

### macOS

```bash
git -C ~/.lighthouse/app fetch origin main
git -C ~/.lighthouse/app reset --hard origin/main
~/.lighthouse/venv/bin/pip install --upgrade ~/.lighthouse/app
launchctl kickstart -k gui/$(id -u)/com.cpym.su.lighthouse
lh migrate
```

### Windows PowerShell

The installer is idempotent and also performs upgrades:

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-windows.ps1 | iex
```

To remove LightHouse while leaving PostgreSQL and its databases untouched:

```powershell
irm https://raw.githubusercontent.com/CPYMSU/LightHouse/main/uninstall-windows.ps1 | iex
```

Then enter a project and run `lh`; LightHouse creates the platform-native System
and Desktop Targets and switches the local workspace to `AUTO`.

See `docs/ARCHITECTURE.md`, `docs/AGENT_RUNTIME.md`, `docs/TERMINAL_UI.md` and
`docs/DESKTOP_KERNEL.md` for internal contracts.
