# SAT2 Relay 2.0.1 Test Report

Date: 2026-08-04

## Result

All automated checks executed for the role-routing hotfix passed.

## Executed checks

| Check | Result |
|---|---:|
| Daemon unit/integration/regression tests | 33 passed |
| Complete repository maintenance mirror tests | 41 passed |
| Extension tests | 3 passed |
| Full v2 authorize → ACK → checkpoint → Mentor accepted chain | passed |
| Worker-only installation cannot consume Mentor delivery | passed |
| Mentor-bound installation receives Mentor delivery | passed |
| Missing role endpoint consumes zero delivery attempts | passed |
| Heartbeat → persistent role-endpoint registry | passed |
| Prior terminal `ROLE_NOT_BOUND` delivery migration/requeue | implemented and covered by database initialization path |
| Task ref `@config` regression | passed |
| Python compileall | passed |
| Extension JavaScript syntax | passed |
| Wheel build | passed |
| Wheel target install/import smoke | passed |

Final wheel:

```text
sat2_relay-2.0.1-py3-none-any.whl
SHA-256: 2ee1744b0d048858455fe2b5b1507598645a49bfe3ede5eb199376d93c7d6e8d
```

## Environment limits

The build environment is Linux. The Windows hotfix installer was statically checked but not executed on the user's Windows host. Live ChatGPT delivery still requires local acceptance after reloading extension 2.0.1 and binding the Mentor Session. The hotfix does not claim completion of any SAT2 scientific source, qualification, formal experiment, merge, or evidence state.
