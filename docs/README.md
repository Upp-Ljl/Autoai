# SAT2 Relay Suite 2.2.0

Browser-first, local-assisted, durable Relay for long-running collaboration among SAT2 ChatGPT Mentor and S1-S4 Sessions.

## What changed from 1.0

- Fixed the task-file ref defect that caused `.sat2/tasks/*.yml` to be fetched from the Worker PR head regardless of where the control file actually lived.
- Task specifications default to `task_ref: "@config"` and can explicitly target default branch, PR head/base, branch, tag, or SHA.
- Task-file and transient GitHub failures are retryable. The same authorization comment is automatically replayed after repair.
- Added v2 causal events with `parent_event_id` and `correlation_id` while retaining v1 compatibility.
- Added DPAPI-backed Windows credential storage, live credential reload, token source/fingerprint diagnostics, and exact deep doctor checks.
- Added a self-healing supervisor, 15-second daemon polling, 30-second extension heartbeat, and short bounded fixed retries.
- Added durable extension delivery history and exact transcript-marker confirmation.
- Added duplicate Session binding detection and three Connector modes.
- Added an optional bounded MCP stdio server for Relay diagnostics and safe control operations.
- Added a redacted one-click diagnostic bundle and bounded JSONL log rotation for long-running use.
- Rebuilt the dashboard and extension UI around explicit failure stages and recovery actions.
- Added role-aware bidirectional routing: Worker checkpoints are leased only by a browser installation that has the target Mentor role bound.
- Added a persistent local role-endpoint registry and automatic requeue of prior `ROLE_NOT_BOUND` delivery failures.

## Windows installation

双击根目录 `INSTALL_WINDOWS.cmd`。命令行安装仍可使用：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\daemon\scripts\install_windows.ps1
```

The installer creates `%LOCALAPPDATA%\SAT2Relay`, installs a supervised scheduled task, copies the extension to a stable directory, optionally stores the GitHub PAT using Windows DPAPI, and prints the local API token.

Load the unpacked extension from:

```text
%LOCALAPPDATA%\SAT2Relay\extension
```

Then bind Mentor and S1-S4 to concrete ChatGPT `/c/<conversation-id>` pages. Project home pages are rejected to prevent ambiguous delivery.

## Daily operation

No terminal is required. Keep the browser open. The extension heartbeats and pumps deliveries automatically; the local supervisor restarts the daemon after a crash.

Deep diagnosis:

```powershell
%LOCALAPPDATA%\SAT2Relay\venv\Scripts\sat2-relay.exe `
  --config %LOCALAPPDATA%\SAT2Relay\config.yml doctor --json
```

## Package map

- `extension/`: Chrome/Edge Manifest V3 extension.
- `daemon/`: local Control Center, supervisor, credential store, dashboard, MCP server, and tests.
- `repo/`: repository-side config/schema/docs/source mirror for a maintenance PR.
- `FINAL_REQUIREMENTS_ZH.md`: authoritative product and acceptance requirements.
- `ARCHITECTURE.md`: runtime architecture and failure isolation.
- `DEBUGGING.md`: deterministic troubleshooting runbook.
- `MIGRATION.md`: 1.0 → 2.0 migration.
- `MCP_SETUP.md`: optional local Agent/MCP configuration.
