# SAT2 Relay 2.2.2 Acceptance Checklist

Required acceptance points:

- Mentor-authored task document is the normal task authorization; no separate Dashboard authorization step is required.
- Task document must have non-empty purpose/objective and acceptance criteria before automatic dispatch.
- Exact task document SHA-256 is frozen at first dispatch; silent contract drift fails closed.
- No handwritten control YAML.
- No manual SHA, parent-event, actor, target or event-ID copying.
- Decision JSON must include the current Capsule `delivery_token`.
- `WORKER_ACK` is not a blocking step for `WORKER_CHECKPOINT`.
- `WORKER_CHECKPOINT` transitions `DISPATCHED` to `MENTOR_REVIEW`.
- `MENTOR_CHANGES_REQUIRED` transitions `MENTOR_REVIEW` to `DISPATCHED`.
- `MENTOR_ACCEPTED` transitions `MENTOR_REVIEW` directly to `COMPLETE` after Relay validation; no mechanical second confirmation is required.
- `WAITING_FOR_ENDPOINT` is a delivery condition, not a Protocol State.
- Worker and Mentor deliveries can only be leased by fresh endpoints bound to the fixed target role.
- Delivery is considered successful only after the exact Relay transcript marker is observed in the target conversation.
- Worker Decision submission is bound to installation, role, conversation, delivery, token, assistant-message hash and current PR head.
- GitHub publish uncertainty is recovered by stable event-marker search before retry.
- `STALE_PR_HEAD` prevents Mentor review publication when PR head changed after checkpoint.
- Manual extension button submits the current Decision through the same validation and idempotency path as automatic detection.
- `绑定 Session 回复完成提醒` remains functional with automatic progression disabled.
- Reply notifications are emitted only for bound conversations, are deduplicated per completed assistant reply, and do not alter Relay Protocol State.
- Real Windows + ChatGPT field acceptance must demonstrate Mentor → Worker → Mentor round-trip with zero cross-role delivery and zero duplicate GitHub control comments.
