# SAT2 Relay 2.0.1 Release Notes

## Critical defect closed

Version 1.0 fetched every monitor task YAML from the monitored PR head SHA. For PR #48 this produced a deterministic 404 even though `.sat2/tasks/P0-B-WP-B.yml` existed on the Relay control branch and on `main`. The error was incorrectly classified as a permanent protocol failure, so the authorization comment could not recover after configuration or credential repair.

Version 2.0 resolves the task spec from an explicit `task_ref`; the default is `@config`, meaning the same reviewed ref as `.sat2/relay.yml`. Task-spec and transient GitHub failures are stored as `retryable_error`. The same GitHub comment is replayed automatically after correction. Existing v1 SQLite records matching the historical task-spec 404 signature are migrated to retryable state.

## Reliability changes

- browser-first automatic Session delivery and 30-second heartbeat;
- 15-second GitHub poll without exponential delay;
- bounded delivery retries at 5/10/20/30 seconds;
- durable SQLite queue and extension marker history;
- exact transcript confirmation before delivery success;
- self-healing local supervisor;
- DPAPI credential store and live credential reload;
- ETag-aware GitHub reads and rate-limit diagnostics;
- v2 causal event chain with `parent_event_id`;
- one-click redacted diagnostic bundle;
- rotating structured JSONL logs;
- optional bounded MCP stdio bridge.

## Deliberate safety boundary

The extension and local process do not merge PRs, dispatch workflows, run qualification/formal experiments, or alter scientific evidence. These remain explicit human gates.
