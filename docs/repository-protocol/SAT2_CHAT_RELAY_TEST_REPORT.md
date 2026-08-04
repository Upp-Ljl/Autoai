# SAT2 Relay Suite 1.0.0 test report

Test date: 2026-07-26

## Sandbox validation passed

- Daemon unit/integration tests: **16 passed**.
  - event schema and parsing;
  - actor, target, PR and SHA binding;
  - strict state transitions;
  - SQLite persistence, deduplication, leasing, retries, dry-run approval and cancellation;
  - local API token authentication and extension heartbeat;
  - first-poll baseline protection;
  - untrusted actor and edited-control-comment blocking;
  - PR path-scope, dependency and parallel write-scope enforcement;
  - Worker-to-Mentor and Mentor-to-Worker routing.
- Browser extension tests: **3 passed**.
  - simulated ChatGPT composer health and delivery;
  - GitHub app attachment path;
  - transcript-level deduplication after content-script reinjection;
  - busy-session blocking;
  - Manifest permission audit;
  - Chromium native extension packing.
- Repository-wide suite: **24 passed**.
- JavaScript `node --check`: passed.
- Python `compileall`: passed.
- Wheel build: passed.
- Wheel import, packaged schema loading and `sat2-relay init`: passed.
- ZIP integrity checks: passed.

## Not verified in this ChatGPT environment

- Windows scheduled-task installation and process lifecycle;
- the user's private-repository token permissions and rate limits;
- the user's authenticated ChatGPT browser DOM;
- the live ChatGPT GitHub app picker/attachment;
- browser and computer restart recovery on the user's machine;
- real GitHub Alert Issue email delivery;
- multi-hour unattended local operation.

These are local acceptance items and are not represented as sandbox-tested.
