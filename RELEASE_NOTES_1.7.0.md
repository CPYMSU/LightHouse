# LightHouse OS 1.7.0

## CodeFoundry routed into production / 正式編程路由

CodeFoundry is now connected to the native LightHouse engineering route through
an explicit feature flag. It is not an unused library: matching coding runs can
execute its typed action loop, evidence ledger, post-patch validation, fresh
diff review, and durable event stream as the authoritative result.

- `LIGHTHOUSE_CODE_FOUNDRY_MODE=off` preserves the existing engineering loop
  and remains the release default.
- `LIGHTHOUSE_CODE_FOUNDRY_MODE=shadow` records a bounded, read-only
  CodeFoundry candidate. Workspace mutations are deliberately withheld and the
  existing loop remains authoritative.
- `LIGHTHOUSE_CODE_FOUNDRY_MODE=on` routes matching coding tasks into the
  evidence-gated CodeFoundry production loop. Non-coding tasks and unsupported
  kernel modes retain their existing route, with the reason recorded durably.

CodeFoundry continues to use LightHouse-owned Python types, provider bridge,
Kernel Operations, Receipts, and AgentStore events. It does not call a Codex
CLI, app-server, MCP server, or runtime protocol.

## Verification / 驗證

- Added integration coverage for `off`, `shadow`, and `on` routing.
- The `on` route proves patch, current diff, validation, and native review
  before an altered workspace reports a verified result.
- Full test suite: 222 passed, 15 skipped.

## Upgrade

Use the normal LightHouse installer or upgrade the local package, then restart
`lh`. To enable the new route for a controlled coding rollout, set
`LIGHTHOUSE_CODE_FOUNDRY_MODE=on` (or `"code_foundry_mode": "on"` in the
local LightHouse configuration).
