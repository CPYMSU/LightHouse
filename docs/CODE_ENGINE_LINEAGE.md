# Code Engine source lineage

## Codex compatibility baseline

- Upstream: `openai/codex`
- Commit inspected: `9d00bb01c0a712fb7c2f5b002bdf33bcc0fc352c`
- Protocol: `codex-rs/app-server` v2, stdio JSONL
- License: Apache-2.0
- Inspection date: 2026-08-06

The Python app-server client is a clean-room implementation of the documented
wire protocol. No Codex CLI, TUI, app-server Rust implementation or product UI
source is copied into LightHouse. The optional Rust kernel is original
LightHouse code informed by public Unified Exec responsibilities: PTY lifecycle,
streaming output, approval/sandbox separation and fail-closed execution.

## Claude Code boundary

Claude Code interaction patterns are treated only as product references. Its
main repository is not used as a source dependency because its license reserves
all rights and points use to Anthropic's commercial terms.

## Maintenance

Protocol compatibility tests must be updated against an explicit Codex commit.
New direct source adaptations require a file-by-file provenance entry, Apache-2.0
notice retention and regression tests before merge.
