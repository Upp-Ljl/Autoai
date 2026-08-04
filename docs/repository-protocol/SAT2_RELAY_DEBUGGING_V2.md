# SAT2 Relay 2.0 Debugging Runbook

## One-command diagnosis

```powershell
%LOCALAPPDATA%\SAT2Relay\venv\Scripts\sat2-relay.exe `
  --config %LOCALAPPDATA%\SAT2Relay\config.yml doctor --json
```

The doctor checks the exact operations used by the daemon, including every monitor's resolved task path/ref. A generic token test is not considered sufficient.

## Error classification

- `TASK_SPEC_UNAVAILABLE`: wrong path/ref or token cannot see that ref. Retryable; same comment is replayed automatically.
- `TASK_SPEC_INVALID`: YAML/task ID/repository/PR/role/path lists disagree. Permanent until reviewed config/spec is corrected and replay requested.
- `GITHUB_AUTH_FAILED`: daemon's current credential cannot read the private repository or PR.
- `PROTOCOL_OR_STATE_INVALID`: malformed event, wrong role/SHA, wrong parent event, or illegal state transition.
- `DELIVERY_FAILED`: browser delivery exceeded the bounded retry count or hit a non-retryable condition.
- `BROWSER_RELAY_OFFLINE`: pending delivery exists but heartbeat is stale.
- `DUPLICATE_SESSION_BINDING`: two roles point at the same ChatGPT conversation.
- `SUBMISSION_MARKER_NOT_CONFIRMED`: the send click occurred but the exact marker did not appear in a user turn; delivery is not marked successful.

## Recovery order

1. Open extension popup and run Deep Doctor.
2. Read the first failed check; do not publish another authorization comment yet.
3. Correct credentials with `sat2-relay credentials set --github-token` or correct `task_ref`.
4. Click Reload Credentials and Poll Now.
5. A comment previously stored as `retryable_error` is replayed automatically.
6. Only a permanent invalid event requires a corrected new GitHub event or explicit replay after review.

## 脱敏诊断包

Control Center 点击“导出诊断包”，或调用：

```text
GET /api/v2/diagnostics/export
```

诊断包不包含 GitHub PAT、本地 API token，也不包含完整任务投递正文；正文仅保留长度和 SHA-256。它适合直接交给开发 Session 定位故障。

日志位置：

```text
%LOCALAPPDATA%\SAT2Relay\logs\sat2-relay.jsonl
```

日志按 10 MiB 自动轮转并保留 5 个备份。
