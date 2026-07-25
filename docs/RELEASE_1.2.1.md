# LightHouse OS 1.2.1

LightHouse OS 1.2.1 is a focused macOS installation reliability patch.

## Fixed

The macOS installer no longer exits immediately when Homebrew reports a
`launchctl bootstrap ... exited with 5` error while starting PostgreSQL 16.

The installer now:

1. reuses PostgreSQL immediately when it is already healthy;
2. attempts the normal Homebrew service startup;
3. safely stops and unloads stale Homebrew service state;
4. runs `brew services cleanup` and retries launchd registration;
5. falls back to `pg_ctl` using the existing Homebrew PostgreSQL 16 data directory;
6. verifies readiness with `pg_isready` before continuing the LightHouse install.

The recovery path never deletes, reinitializes or replaces the PostgreSQL data
directory. If all startup methods fail, installation stops with a concise error
and points to `~/.lighthouse/logs/postgresql.log` while preserving existing data.

## Install or upgrade on macOS

```bash
curl -fsSL https://raw.githubusercontent.com/CPYMSU/LightHouse/main/install-macos.sh | bash
```
