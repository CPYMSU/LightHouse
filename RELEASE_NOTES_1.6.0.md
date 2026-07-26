# LightHouse OS 1.6.0

## CodeFoundry / 原生編程生產線

LightHouse 1.6.0 introduces CodeFoundry: a native, evidence-first coding loop
for repository work. It uses LightHouse's own provider bridge, Kernel
Operations, Receipts, AgentStore, and Python domain types; it does not launch
or depend on a Codex CLI, app-server, MCP server, or runtime protocol.

- A bounded coding brief, typed history, action registry, parallel read runtime,
  deterministic tool context, and UTF-8-safe observation truncation keep each
  coding turn focused on the active repository work.
- Successful patches advance a workspace generation. Current diff, validation,
  and native review evidence are required before a changed run can report a
  verified result.
- Patch Receipts now carry changed paths, so stale file observations and earlier
  proof cannot satisfy the current tree.
- CodeFoundry lifecycle events are persisted through the existing AgentStore.
  Its model adapter reuses the LightHouse JSON provider while exposing only the
  small coding-tool surface and its typed arguments.

## Source lineage / 開源來源沿革

Three bounded algorithms were translated and materially modified from the
public OpenAI Codex source snapshot at commit
`61a44880a85d2fd0d8770908dea5733495e571c8`: tool-context deltas, UTF-8-safe
output truncation, and patch-path accounting. Each adaptation carries its
source header and regression tests. They are distributed under Apache-2.0 with
the required attribution and license copy in `THIRD_PARTY_NOTICES.md` and
`THIRD_PARTY_LICENSES/Apache-2.0.txt`.

這不是包裝或遠端呼叫 Codex；CodeFoundry 的介面、狀態、驗證、持久化和後續演進
全部由 LightHouse 自己擁有。

## Compatibility

- Existing workspaces, AgentStore records, Kernel Receipts, configuration, and
  provider settings remain compatible.
- No PostgreSQL migration is required for this release.
- Upgrade existing installations with the normal LightHouse installer, then
  restart `lh` to load version 1.6.0.
