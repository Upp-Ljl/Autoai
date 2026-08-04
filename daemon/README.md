# SAT2 Relay local Control Center 2.2

The local process is deliberately small: GitHub read/validation, SQLite state, loopback API, deterministic Decision publication, diagnostics, and a bounded optional MCP bridge. Browser delivery remains owned by the extension.

On Windows, use the repository-level [`scripts/windows/INSTALL_ON_DEMAND.ps1`](../scripts/windows/INSTALL_ON_DEMAND.ps1). There is deliberately no daemon-level login-start installer.

## Commands

```bash
sat2-relay init
sat2-relay credentials set --github-token
sat2-relay doctor --json
sat2-relay poll-once
sat2-relay serve
sat2-relay supervise
sat2-relay status --json
sat2-relay-mcp
```

## Credential precedence

1. local credential store (`credentials.bin`); then
2. configured environment variable.

Windows credential data is protected with current-user DPAPI. Secret values never appear in status, logs, extension storage, or MCP output.

## Safety

The local agent does not make scientific decisions, merge, dispatch workflows, run qualification/formal experiments, or edit scientific evidence. Those remain explicit human gates.

## Redacted diagnostics

```text
GET http://127.0.0.1:8765/api/v2/diagnostics/export
```

The endpoint requires the local API token and excludes GitHub secrets, the local API token, and full delivery bodies. JSONL logs rotate at 10 MiB with five backups.

## Optional MCP

See the suite-level `MCP_SETUP.md`. The MCP server is stdio-only and forwards a bounded set of authenticated requests to the loopback daemon.
