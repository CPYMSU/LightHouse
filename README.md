# LightHouse OS

LightHouse is one integrated PostgreSQL-first AI operating terminal with three
governed execution surfaces:

- **Data Kernel** — PostgreSQL and structured business data;
- **System Kernel** — files, code, Git, tests, shell and Linux servers;
- **Desktop Kernel** — semantic macOS application, browser and file launching.

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

The installer prepares Python 3.12, PostgreSQL 16 and Git, downloads the complete
LightHouse package, asks for the model endpoint/name and a hidden API key, stores
credentials in macOS Keychain, installs a `launchd` background service and the
single `lh` command, then runs migrations and health checks.

No API key is written to the repository or `~/.lighthouse/config.json`.

## Swiss Super Terminal

Open a project directory and run:

```bash
cd /path/to/your/project
lh
```

LightHouse automatically binds a confined local System Target and Desktop Target
and selects `AUTO`, so one goal can cross both surfaces:

```text
lh> 製作一個 Swiss 風格的 dashboard.html，然後在 Safari 打開
```

The governed chain is:

```text
System Kernel: inspect project -> create/apply HTML change -> Receipt
Desktop Kernel: resolve dashboard.html inside allowed root -> macOS open -> Receipt
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

```bash
lh "create an HTML dashboard and open it in Safari"
```

## Desktop Kernel 0.5

The first Desktop Kernel slice uses macOS Launch Services (`/usr/bin/open`) rather
than brittle mouse coordinates. It exposes exact capabilities:

- `desktop.browser.open_url.v1`
- `desktop.file.open.v1`
- `desktop.app.open.v1`

Targets explicitly constrain project/file roots, URL schemes and applications.
Opening an HTTP/HTTPS URL or a confined project file is a low-risk direct
capability. Launching an allow-listed application creates an explicit
confirmation Operation.

Browser page interaction is intentionally not simulated with mouse coordinates
in this slice. A future Playwright/CDP adapter can add semantic DOM navigation,
form filling and downloads behind the same Capability → Operation → Receipt
contract.

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
PostgreSQL after a disconnect. The model never receives raw filesystem, shell,
database or desktop authority.

## Useful commands

```bash
lh                              # Swiss interactive terminal
lh "task"                       # one natural-language task
lh init [PATH]                  # bind System + Desktop targets
lh login                        # replace the model key in macOS Keychain
lh doctor                       # verify installation
lh capabilities                 # inspect all active capabilities
lh capabilities --kernel desktop
lh mode auto|data|system|desktop
lh run desktop.browser.open_url.v1 \
  --mode desktop \
  --args-json '{"url":"https://example.com","browser":"Safari"}'
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
- API keys are stored in macOS Keychain.
- High-risk writes freeze an exact Operation before confirmation.
- Receipts, not client timeouts, are the source of execution truth.
- Local System and Desktop targets are confined to explicit allowed roots.
- Desktop URL schemes and applications are allow-listed per target.
- SSH host-key checking remains strict by default.

## Upgrade an existing installation

```bash
git -C ~/.lighthouse/app fetch origin main
git -C ~/.lighthouse/app reset --hard origin/main
~/.lighthouse/venv/bin/pip install --upgrade ~/.lighthouse/app
launchctl kickstart -k gui/$(id -u)/com.cpym.su.lighthouse
lh migrate
```

Then enter the project and run `lh`; LightHouse creates its Desktop Target and
switches the local workspace to `AUTO`.

See `docs/ARCHITECTURE.md`, `docs/AGENT_RUNTIME.md`, `docs/TERMINAL_UI.md` and
`docs/DESKTOP_KERNEL.md` for internal contracts.
