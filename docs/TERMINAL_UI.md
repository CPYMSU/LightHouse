# LightHouse Swiss Terminal UI

## Design lineage

The terminal borrows the visual discipline of Warehouse OS 2.0 rather than its
browser implementation. The native Mac interface is original Python code built
with Rich and Prompt Toolkit.

The shared visual language is:

- paper / ink / red contrast;
- strict horizontal and column grids;
- uppercase micro-labels;
- one red accent reserved for identity, active execution and risk;
- compact capability and execution rows;
- explicit pending-confirmation state;
- visible steps and durable Receipt results.

## Boundary

`SwissTerminal` is presentation-only. It may display immutable Operation and
agent-run state and collect user input, but it may not grant authority, choose a
target, mutate a Receipt or bypass confirmation. All effects remain owned by the
Operation Kernel and deterministic executors.

## Main shell

`lh` opens the Swiss shell and binds the current directory to a confined local
workspace when necessary. The masthead shows:

- active kernel mode;
- workspace;
- project path;
- LightHouse Brain state;
- local control-plane status.

Prompt Toolkit provides persistent local history, suggestions, slash-command
completion and a compact bottom toolbar. History contains user commands, so it
must never be used to enter credentials. Model credentials continue to use
macOS Keychain and hidden input.

## Run timeline

Durable `lh_agent_steps` are mapped to a stable visual sequence:

| Step kind | UI label |
|---|---|
| `run_created` | PLAN |
| `project_context` | CONTEXT |
| `decision` | THINK |
| `operation_dispatched` | EXECUTE |
| `auto_confirmation` | CONFIRM |
| `observation` | VERIFY |
| `input_required` | INPUT |
| `run_completed` | COMPLETE |
| protocol/provider/tool errors | ERROR / REJECTED |

The UI renders only newly observed steps during one terminal session. PostgreSQL
remains the state source after reconnecting.

## Confirmation card

A confirmation card must display the exact Operation ID, capability, kernel,
target and frozen arguments. It must state that no write has occurred. The card
may only call the existing confirmation endpoint for that Operation.

## Receipt card

Receipt views distinguish verified success from failure and include the result
hash prefix. Large structured results remain bounded by executor and provider
limits before presentation.

## Local controls

Local slash commands do not enter the model and never execute automatically:

- `/help`
- `/status`
- `/capabilities [query]`
- `/mode auto|system|data`
- `/init [path]`
- `/doctor`
- `/login`
- `/clear`
- `/exit`

An exclamation prefix passes an exact compatibility command to the existing CLI
parser. It does not bypass capability, target, risk or confirmation rules.
