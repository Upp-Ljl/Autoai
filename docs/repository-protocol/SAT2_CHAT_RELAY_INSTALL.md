# SAT2 Relay installation and operation

## 1. Install the local daemon

Requirements:

- Python 3.11 or later;
- network access to GitHub;
- a fine-grained GitHub token with read access to repository metadata, contents, pull requests, and issues.

### Windows

Run PowerShell as the current user:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\tools\sat2_relay\scripts\install_windows.ps1
```

The installer creates `%LOCALAPPDATA%\SAT2Relay`, a Python virtual environment, a local config file, and an `ONLOGON` scheduled task.

Set a user environment variable:

```powershell
[Environment]::SetEnvironmentVariable("SAT2_GITHUB_TOKEN", "<fine-grained-token>", "User")
```

An optional separate alert token can be set as `SAT2_GITHUB_ALERT_TOKEN`.

### Linux

```bash
bash tools/sat2_relay/scripts/install_linux.sh
```

Then place tokens in `~/.config/sat2-relay/environment`:

```text
SAT2_GITHUB_TOKEN=...
SAT2_GITHUB_ALERT_TOKEN=...
```

Restart:

```bash
systemctl --user restart sat2-relay.service
```

## 2. Local security

The daemon:

- binds only to `127.0.0.1`;
- requires a random local API token;
- stores queue/state in SQLite;
- reads GitHub tokens from environment variables;
- does not put a GitHub token in the browser extension;
- does not expose an MCP or public network endpoint.

The config is normally located at:

```text
Windows: %LOCALAPPDATA%\SAT2Relay\config.yml
Linux:   ~/.config/sat2-relay/config.yml
```

## 3. Install the browser extension

1. Open `chrome://extensions/`.
2. Enable Developer mode.
3. Choose **Load unpacked**.
4. Select `tools/sat2_relay_extension`.
5. Open extension settings.
6. Set the daemon URL to `http://127.0.0.1:8765`.
7. Copy `server.api_token` from the local daemon config.
8. Test the connection.
9. Bind the existing Mentor and Worker ChatGPT conversations.

## 4. Initial safe activation

Repository `.sat2/relay.yml` initially uses:

```yaml
enabled: false
mode: shadow
monitors: []
```

Do not enable active routing until:

- daemon `doctor` succeeds;
- browser extension reports a heartbeat;
- one Mentor and one Worker are bound;
- GitHub app attachment is confirmed;
- one dry-run event chain succeeds;
- local restart recovery is tested.

## 5. Commands

```bash
sat2-relay init
sat2-relay doctor
sat2-relay poll-once
sat2-relay serve
```

Dashboard:

```text
http://127.0.0.1:8765/
```

## 6. Enabling a task monitor

Add a monitor only in a reviewed GitHub change:

```yaml
monitors:
  - pr_number: 40
    task_id: WP-B6
    worker_role: S4
    enabled: true
    required_apps: [GitHub]
    strict_apps: true
    task_file: .sat2/tasks/WP-B6.yml
    allowed_paths: [mvp/single_sat_sim_v2/runtime/**, tests/single_sat_sim_v2/runtime/**, doc/WP-B6.md, .sat2/tasks/WP-B6.yml]
    forbidden_paths: [paper/**, outputs/**, .github/workflows/**]
    dependencies: [WP-B4]
```

Then change mode in stages:

```text
shadow → dry_run → active
```

## 7. Alert Issue

Create one long-lived issue named `[SAT2 Relay] Automation Alerts`, assign it to the user, and record its number in `.sat2/relay.yml`.

For guaranteed email-style GitHub notification, configure the alert writer as a separate low-permission bot/GitHub App that mentions the user. The primary personal token should remain read-only whenever possible.
