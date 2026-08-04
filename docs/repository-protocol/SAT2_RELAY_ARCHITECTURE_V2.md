# SAT2 Relay 2.0 Architecture

```text
GitHub authoritative control comments + task specs
                    │
                    ▼
Local Control Center
  GitHub client ─ protocol/state/SHA/scope validation
       │          SQLite durable state
       │          short fixed retries
       │          diagnostics + doctor
       ▼
Loopback API 127.0.0.1:8765
                    │
                    ▼
Chrome/Edge extension service worker
  30 s heartbeat + delivery pump
  durable marker history
  role/session binding health
                    │
                    ▼
ChatGPT content script
  resilient composer discovery
  optional Connector attachment
  exact transcript-marker confirmation
                    │
                    ▼
Mentor / S1 / S2 / S3 / S4 Sessions
                    │
                    └──── publish next GitHub Relay event
```

## Failure isolation

- GitHub/task spec failure: comment remains `retryable_error`; no browser delivery is created.
- Protocol/state failure: comment becomes permanent `invalid_event`; exact reason is displayed.
- Browser unavailable: delivery remains retryable in SQLite.
- Session busy: short fixed retry; no duplicate message.
- Browser/daemon crash: supervisor and persistent state resume without replaying delivered markers.
- Credentials changed: `reload-credentials` replaces GitHub clients without daemon restart.
