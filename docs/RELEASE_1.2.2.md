# LightHouse OS 1.2.2

LightHouse OS 1.2.2 is a focused bootstrap and local-service reliability patch.

## Fixed

### GitHub download bootstrap

The macOS install instructions no longer depend on a successful TLS connection to
`raw.githubusercontent.com`. The public command uses the GitHub API raw-media endpoint.
Once the installer starts, it downloads into a temporary file and automatically tries:

1. `raw.githubusercontent.com`;
2. `api.github.com` with the GitHub raw-media Accept header.

Each endpoint is retried, and the downloaded file must be non-empty, have the expected
Bash shebang, and pass `bash -n` before it can execute. The same fallback is used when a
fresh Mac needs to download the Homebrew installer.

### Local LightHouse service recovery

The macOS installer now retries stale launchd registration for the default LightHouse
service before declaring installation unsuccessful.

After installation, a CLI request that receives a loopback `ConnectError` or
`ConnectTimeout` wakes the configured default platform service, verifies `/healthz`,
and retries the original request once. Read timeouts and other ambiguous failures are
never retried, preventing duplicate side effects.

No Workspace, Target, authority, Operation or database semantics changed.
