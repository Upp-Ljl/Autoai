# Migration from 1.0 to 2.0

1. Run the 2.0 Windows installer. It removes the legacy scheduled task and installs the supervised task.
2. Existing `config.yml` and SQLite are retained unless explicitly deleted.
3. SQLite schema migration is automatic.
4. v1 Relay event comments remain readable.
5. Add `task_ref: "@config"` to each monitor. It is also the 2.0 default.
6. Store the PAT once with:

```powershell
sat2-relay --config "$env:LOCALAPPDATA\SAT2Relay\config.yml" credentials set --github-token
```

7. Reload the unpacked extension from `%LOCALAPPDATA%\SAT2Relay\extension`.
8. Rebind each role to a concrete ChatGPT `/c/<conversation-id>` page; project home pages are intentionally rejected.
9. Run Deep Doctor before enabling active mode.
