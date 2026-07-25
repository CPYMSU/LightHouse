# LightHouse Instance Kernel

LightHouse 0.8.1 can run multiple local API instances at the same time without
splitting the durable Data or Memory Kernel.

## Isolation model

Every instance has its own:

- loopback API port;
- configuration file;
- server logs;
- project and Workspace binding;
- active conversation pointer;
- process lifecycle record.

All instances reuse the installed LightHouse runtime, native secret store and the
same PostgreSQL database. Receipts, long-term memory, workspaces and background
queues therefore remain one coherent system. PostgreSQL work and background jobs
already use transactional claims, including `FOR UPDATE SKIP LOCKED`, so multiple
API processes do not execute one queued job twice.

Instance records live under:

```text
~/.lighthouse/instances/
├── default/instance.json
├── research/
│   ├── config.json
│   ├── instance.json
│   └── logs/
└── coding/
    ├── config.json
    ├── instance.json
    └── logs/
```

The default instance remains owned by `launchd` on macOS or the `LightHouse`
Scheduled Task on Windows. Additional instances are detached local processes
managed by the `lh` command.

## Commands

Start another instance and enter it immediately:

```text
lh new
lh new research
lh new coding --project /path/to/project
lh new warehouse --project C:\work\warehouse
```

Start without entering the interactive terminal:

```text
lh new research --no-attach
```

Inspect, attach, stop and restart:

```text
lh instances
lh attach research
lh stop research
lh start research
lh --instance research doctor
lh --instance research "continue the current task"
```

## Port allocation

The default preferred port is `8787`. Installation and `lh new` scan upward until
a free loopback port is found. A conflicting port is therefore an allocation
signal, not an installation failure.

The chosen port is written once to that instance's config and becomes the single
source of truth for the API server, CLI, health checks and diagnostics.

## Safety boundary

- Every API binds to `127.0.0.1` by default.
- Control and model credentials remain in macOS Keychain or Windows DPAPI.
- Instance configs contain no model API key.
- Additional instances cannot expand System or Desktop Target roots.
- Stopping one additional instance does not stop PostgreSQL or another instance.
- Uninstallers stop registered additional processes before removing the runtime.
