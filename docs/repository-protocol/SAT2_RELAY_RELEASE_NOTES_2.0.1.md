# SAT2 Relay 2.0.1 Role-Routing Hotfix

## Fixed failure

Relay 2.0 correctly mapped `SAT2_WORKER_CHECKPOINT` to logical role `mentor`, but the delivery queue was leased globally rather than by the requesting browser installation's bound roles. A browser profile without the Mentor binding could lease the checkpoint and report `ROLE_NOT_BOUND`; 2.0 treated that result as non-retryable and permanently failed the delivery.

## Changes

- Added a durable local `role_endpoints` registry derived from extension heartbeats.
- Delivery leasing is now installation- and role-aware.
- An extension can lease only deliveries for roles currently bound in that extension installation.
- Missing/stale endpoints do not consume a delivery attempt.
- `ROLE_NOT_BOUND` is retryable as a final race-condition safeguard.
- Existing failed `ROLE_NOT_BOUND` / `ROLE_ENDPOINT_UNAVAILABLE` deliveries are automatically requeued during database migration.
- Deep Doctor now verifies global role routing across browser installations, detects missing endpoints, duplicate conversations, and ambiguous duplicate role endpoints.
- Daemon 2.0.1 accepts compatible extension versions in the 2.x line; extension 2.0.1 accepts compatible daemon versions in the 2.x line.

No GitHub checkpoint repost is required. No repository scientific source, experiment, evidence, or PR state is changed by this local hotfix.
