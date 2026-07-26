# LightHouse source notice

LightHouse is implemented as one native Python package.

The built-in reasoning loop, capability routing, PostgreSQL state, Operation
Kernel, executors, confirmation flow and Receipt recovery are all LightHouse
components. No Codex, Claude Code or other third-party agent runtime is bundled
or separately installed.

Most CodeFoundry modules are original LightHouse code. Two bounded Python
adaptations of OpenAI Codex algorithms are included: incremental tool-context
rendering and UTF-8-safe tool-output truncation. They do not carry Codex's
runtime, CLI, protocol, service, model interface, or UI. Their upstream commit,
modified-file notices, tests, and Apache-2.0 attribution are recorded in
`docs/CODE_FOUNDRY_LINEAGE.md` and `THIRD_PARTY_NOTICES.md`.
