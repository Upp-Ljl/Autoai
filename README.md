# SAT2 Relay 2.2 — Local Closed Loop

SAT2 Relay is a local, single-user collaboration relay for role-bound ChatGPT sessions. It reads valid GitHub control events, delivers a guidance Capsule to the correct bound session, accepts a small Decision JSON response, and deterministically publishes the next control event when local publishing is enabled.

This repository is the implementation and maintenance home for SAT2 Relay. The SAT2 scientific repository remains `Upp-Ljl/sat2`; task specifications, PR state, exact SHAs, experiments and evidence remain authoritative there.

The Relay transports and validates collaboration decisions; it does not make scientific decisions, merge code, dispatch workflows, run formal experiments, or modify evidence and papers.

## Repository layout

- `daemon/` — Python loopback daemon, SQLite recovery state, GitHub client, dashboard, schemas, and tests.
- `extension/` — Chrome/Edge Manifest V3 extension.
- `scripts/windows/` — supported Windows **on-demand** installer, Start/Stop scripts, and bounded Native Messaging bootstrap host.
- `sat2AI协作方式.md` — project collaboration policy, updated for Relay 2.2.
- `docs/SAT2_CHAT_RELAY_PROTOCOL.md` — current closed-loop Session/Relay protocol.
- `docs/RELAY_2.2_OPERATION.md` — exact local operating procedure.
- `docs/repository-protocol/` — retained Relay 1.x/2.0 historical material; not the current contract.

## Windows: on-demand installation

Do not use a login-start scheduled task. From a clone of this repository, run:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File .\scripts\windows\INSTALL_ON_DEMAND.ps1 `
  -DataRoot "D:\SAT2RelayData"
```

The installer creates `%LOCALAPPDATA%\SAT2Relay` for the program and configuration, and puts SQLite and logs under `DataRoot`. It removes legacy Relay scheduled tasks, creates desktop Start/Stop shortcuts, compiles the bounded Native Messaging host, and starts Relay once for initial extension setup. It does not register a login startup task.

Load the unpacked extension from `%LOCALAPPDATA%\SAT2Relay\extension`. Configure `http://127.0.0.1:8765`, paste the local API token printed by the installer, and bind each role to a concrete ChatGPT `/c/<conversation-id>` page.

### One-time browser/native pairing

The extension can start the Windows Relay only after its current Chromium extension ID is registered with the local Native Messaging host. The popup always shows the current Extension ID. Run once after loading/reloading the unpacked extension:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File "$env:LOCALAPPDATA\SAT2Relay\on-demand\REGISTER_NATIVE_HOST.ps1" `
  -ExtensionId <32-character-extension-id>
```

`INSTALL_ON_DEMAND.ps1` also accepts `-ExtensionId <id>` when the ID is already known. Registration is per-user under the Chrome and Edge Native Messaging host registry keys. The host accepts only `status` and `ensure_running`; it cannot execute arbitrary commands and never receives the GitHub PAT.

## Daily use

Primary path:

```text
Open SAT2 Relay extension
→ click “一键启动协作”
→ Native host starts Relay if needed
→ extension enables automatic progression
→ first heartbeat / poll / delivery cycle runs
```

Desktop fallback remains available:

```text
SAT2 Relay - Start or Repair  → start supervisor, health check, poll once
SAT2 Relay - Stop             → stop all Relay processes
```

Windows login still starts no Relay process. The Native Messaging helper is launched only when the extension explicitly requests it and exits after servicing the request.

## Session contract

A Session that receives a Capsule does not handwrite Relay YAML. It returns a small Decision JSON containing the current `delivery_token`, `decision` and `summary`. Relay derives task, PR, current head, parent/correlation IDs, actor, target and timestamp, validates them, publishes the control comment and routes the next Capsule.

See `docs/SAT2_CHAT_RELAY_PROTOCOL.md` for the normative contract.

## Safety boundary

`github.allow_writes` is `false` after a new installation. Enable it only after role bindings and Deep Doctor pass. Enabling it authorizes deterministic control-comment publication only; it does not authorize merge, workflow dispatch, qualification, formal experiments, evidence changes, registry/seed changes, or paper changes.

Initial task authorization and `MENTOR_ACCEPTED` remain local human-confirmation gates by default.

## Acceptance status

The 2.2 closed-loop code and the 2.2.1 one-click bootstrap path are implemented in source. A fresh Windows installation still requires real-session acceptance: register the current extension ID with the Native Messaging host, click “一键启动协作”, verify Deep Doctor, then verify one Decision JSON is published and routed exactly once.

Until that real round trip is observed, describe the deployment as implemented and ready for field acceptance, not as long-term unattended operation already validated.
