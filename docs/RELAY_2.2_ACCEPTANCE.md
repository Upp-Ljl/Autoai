# SAT2 Relay 2.2 Acceptance Checklist

Required acceptance points:

- No handwritten control YAML.
- No manual SHA or parent-event copying.
- Decision JSON must include the current Capsule `delivery_token`.
- `WORKER_CHECKPOINT` transitions `DISPATCHED` to `MENTOR_REVIEW`.
- `MENTOR_CHANGES_REQUIRED` transitions `MENTOR_REVIEW` to `DISPATCHED`.
- `MENTOR_ACCEPTED` transitions `MENTOR_REVIEW` to `COMPLETE` only after local human confirmation.
- `WAITING_FOR_ENDPOINT` is a delivery condition, not a Protocol State.
- GitHub publish uncertainty is recovered by event marker search before retry.
- `STALE_PR_HEAD` prevents Mentor review publication when PR head changed after checkpoint.
- Manual extension button submits the current Decision through the same validation and idempotency path as automatic detection.
