# Relay 2.2 operating protocol

This document is the operational supplement to `sat2AI协作方式.md`. It replaces any conflicting Relay 2.0 instruction. It does not change scientific governance or the human gates in the collaboration policy.

## Authority order

For an active Capsule, use the following order of facts:

1. current GitHub PR, exact base/head SHA, and task state;
2. the task YAML at the Capsule `task_ref`;
3. the Capsule and its source event/comment;
4. the referenced checkpoint or handoff;
5. the collaboration policy and historical documents.

SQLite, extension storage, chat memory, and historic prose are recovery aids, not authoritative project facts.

## Session output contract

A Session that received a Relay 2.2 Capsule must provide its normal concise scientific report and exactly one Decision JSON marker. It must not write Relay YAML, a target role, a SHA, a parent event ID, or an event ID.

````markdown
<!-- SAT2_RELAY_DECISION -->
```json
{
  "delivery_token": "copy exactly from the current Capsule",
  "decision": "WORKER_CHECKPOINT",
  "summary": "One concise, evidence-bounded statement of the outcome."
}
```
````

Valid decisions are `WORKER_ACK`, `WORKER_CHECKPOINT`, `MENTOR_CHANGES_REQUIRED`, `MENTOR_ACCEPTED`, and `TASK_BLOCKED`. The daemon rejects a missing or used token, a role mismatch, an illegal state transition, a stale PR head, or an ambiguous endpoint.

## Deterministic publishing and routing

When local `github.allow_writes` is enabled, the daemon validates the Decision and derives every control field from local state and GitHub facts: task, PR, SHA, parent/correlation event IDs, actor, target, timestamp, and schema-valid YAML. It writes the GitHub comment, recovers an uncertain publish by searching its marker, and routes the resulting Capsule to the bound target role.

The daemon may publish a control event only after validation. It may not infer a scientific decision from natural language. A target endpoint that is absent, stale, or temporarily offline leaves the delivery waiting; it does not consume attempts or become a permanent failure solely for that reason.

## State and gates

```text
DISPATCHED → WORKER_CHECKPOINT → MENTOR_REVIEW
MENTOR_REVIEW → MENTOR_CHANGES_REQUIRED → DISPATCHED
MENTOR_REVIEW → MENTOR_ACCEPTED → COMPLETE (after local human confirmation)
```

`WAITING_FOR_ENDPOINT` is a delivery condition, not a scientific or protocol state. `WORKER_ACK` is informational and does not block the loop.

The following are never enabled by Relay control-comment publication: merge, branch/base changes, workflow dispatch, qualification, formal experiments, accepted-evidence changes, registry/seed changes, or paper changes. Initial task authorization and high-risk actions remain current human decisions.

## On-demand runtime

Windows login starts no Relay process and registers no Relay scheduled task. **Start or Repair** starts the supervisor, performs a health check, and polls once. **Stop** removes Relay processes. The MV3 extension can resume heartbeat and delivery while the daemon is running, but cannot launch Windows processes when it is stopped.

## Required field acceptance

Before relying on automatic progression in a new installation, verify unique active Mentor and Worker bindings, Deep Doctor, and one real Decision JSON → GitHub event → target Capsule round trip. Until that round trip has been observed, describe the installation as ready for acceptance, not as field-proven.
