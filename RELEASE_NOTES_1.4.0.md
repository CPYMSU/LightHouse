# LightHouse OS 1.4.0

LightHouse OS 1.4.0 adds **Cognitive Continuity** and **Observable Engineering Progress** to the existing autonomous engineering runtime.

## Cognitive Continuity

The main AI can now attach concise, user-visible summaries of its current understanding, important findings, engineering decisions, failures and next intent. These summaries never expose private chain-of-thought or raw hidden deliberation.

The same durable Cognitive State is injected into later model decisions, so the main AI can continue from its current goal, strategy, verified facts, open questions, active files, validation state and latest user direction instead of reconstructing the task from raw history.

## Observable Engineering Progress

The existing Swiss terminal now supports four observation densities:

- `focus` for major direction, code and verification events;
- `balanced` for meaningful cognitive and engineering progress;
- `verbose` for detailed tool, Agent and Token activity;
- `off` for permission, critical failure and final-result surfaces only.

New controls include:

```text
/observe off|focus|balanced|verbose
/cognition
/steer <direction>
lh steer <run-id> "direction"
```

## Evidence-grounded display

Tool activity is narrated from durable decisions and Receipts. File changes, tests, Diff review, database actions, services and verified failures are displayed as structured activity rather than raw JSON.

Credentials, authorization headers, API keys, access tokens and database passwords are redacted before cognitive data is persisted or displayed.

## Compatibility

No PostgreSQL migration is required. The system continues to use the existing `lh_agent_steps` sequence as the only durable event source. Existing Workspaces, Targets, Operations, Receipts, conversations, Memory Fabric, Agent Bus, Mega Projects and Run-wide Auto authority remain compatible.
