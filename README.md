# SAT2 Relay 2.2 — Local Closed Loop

SAT2 Relay is a local, single-user collaboration relay for role-bound ChatGPT sessions. It reads valid GitHub control events, delivers a guidance Capsule to the correct bound session, accepts a small Decision JSON response, and deterministically publishes the next control event when local publishing is enabled.

The Relay transports and validates collaboration decisions; it does not make scientific decisions, merge code, dispatch workflows, run formal experiments, or modify evidence and papers.

## Repository layout

- `daemon/` — Python loopback daemon, SQLite recovery state, GitHub client, dashboard, schemas, and tests.
- `extension/` — Chrome/Edge Manifest V3 extension.
- `scripts/windows/` — supported Windows **on-demand** installer and Start/Stop scripts.
- `sat2AI协作方式.md` — project collaboration policy, updated for Relay 2.2.
- `docs/RELAY_2.2_OPERATION.md` — exact local operating protocol and Decision JSON contract.

## Windows: on-demand installation

Do not use a login-start scheduled task. From a clone of this repository, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\windows\INSTALL_ON_DEMAND.ps1 `
  -DataRoot "D:\SAT2RelayData"
```

The installer creates `%LOCALAPPDATA%\SAT2Relay` for the program and configuration, and puts SQLite and logs under `DataRoot`. It removes legacy Relay scheduled tasks, creates desktop Start/Stop shortcuts, and starts Relay once for initial extension setup. It does not register a login startup task.

Load the unpacked extension from `%LOCALAPPDATA%\SAT2Relay\extension`. Configure `http://127.0.0.1:8765`, paste the local API token printed by the installer, and bind each role to a concrete ChatGPT `/c/<conversation-id>` page.

Daily use:

```text
SAT2 Relay - Start or Repair  → start supervisor, health check, poll once
SAT2 Relay - Stop             → stop all Relay processes
```

The extension cannot cold-start Windows processes while Relay is off; Native Messaging is intentionally not part of this release.

## Safety boundary

`github.allow_writes` is `false` after a new installation. Enable it only after role bindings and Deep Doctor pass. Enabling it authorizes deterministic control-comment publication only; it does not authorize merge, workflow dispatch, formal experiments, evidence changes, or paper changes.

## Acceptance status

The bundled 2.2 unit checks are retained in the source tree. A fresh Windows installation still requires real-session acceptance: bind unique Mentor and Worker endpoints, run Deep Doctor, then verify one Decision JSON is published and routed exactly once.
