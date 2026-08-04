# SAT2 Chat Relay Protocol v1

## 1. Purpose

This protocol defines a machine-readable interface among GitHub, the local SAT2 Relay Control Center, the browser bridge, Mentor, and Worker sessions. It does not replace scientific judgment. GitHub remains authoritative.

The protocol is fail closed:

- missing or malformed fields are not guessed;
- untrusted authors are ignored and alerted;
- invalid or out-of-order state transitions are blocked;
- edited control comments are treated as incidents;
- no relay event can authorize merge, formal workflow execution, registry/seed/evidence mutation, or claim expansion.

## 2. Components

```text
GitHub                authoritative state and machine events
SAT2 Control Center   polling, validation, state machine, SQLite, retries, alerts
Browser extension     existing ChatGPT session binding and verified delivery
Mentor session        authorization, review, scientific decisions
Worker session        bounded implementation, validation, checkpoint
```

The Control Center and extension are deterministic local software. They are not AI agents.

## 3. Repository configuration

`.sat2/relay.yml` contains only non-secret orchestration configuration. Secrets remain in the local daemon environment.

Important fields:

```yaml
protocol_version: sat2-relay/v1
enabled: false
mode: shadow
trusted_actors: [Upp-Ljl]
alert_issue: null
monitors: []
```

Modes:

| Mode | Behavior |
|---|---|
| `shadow` | Parse and validate; record what would be dispatched; send nothing. |
| `dry_run` | Create a held delivery that requires local approval. |
| `active` | Dispatch validated low-risk relay events automatically. |
| `paused` | Do not poll or dispatch. |

## 4. One task, one PR, one Worker

A phase may contain several parallel Worker PRs. Each active task must still have:

- one `task_id`;
- one Worker role;
- one Worker PR;
- one exact base SHA;
- one non-overlapping allowed-path set;
- one Mentor gate.

Parallel tasks are allowed only when dependencies are accepted and write scopes do not overlap. Each enabled monitor must name its task file, allowed paths, forbidden paths, and dependencies. On authorization the daemon verifies the task file and dependency states; on checkpoint/review it verifies every PR changed path against the configured scope.

## 5. Event envelope

A control event must be included in a top-level PR conversation comment using one of these forms:

````markdown
<!-- SAT2_RELAY_EVENT_V1 -->
```yaml
protocol: sat2-relay/v1
event_id: WP-B3-checkpoint-001
event_type: SAT2_WORKER_CHECKPOINT
repository: Upp-Ljl/sat2
task_id: WP-B3
actor_role: S2
pr_number: 32
candidate_sha: 0123456789abcdef0123456789abcdef01234567
attempt: 1
timestamp: 2026-07-26T15:00:00+09:00
summary: M1 implementation candidate ready for Mentor review
```
````

or:

````markdown
```sat2-relay
protocol: sat2-relay/v1
...
```
````

The JSON Schema is `.sat2/schemas/relay-event-v1.schema.json`.

## 6. Event types

| Event | Source | Normal target | Meaning |
|---|---|---|---|
| `SAT2_TASK_AUTHORIZED` | Mentor | Worker | A bounded task may begin. |
| `SAT2_WORKER_ACK` | Worker | none | Worker received and validated the capsule; no additional wake-up is required. |
| `SAT2_WORKER_CHECKPOINT` | Worker | Mentor | Exact candidate is ready for review. |
| `SAT2_MENTOR_CHANGES_REQUIRED` | Mentor | same Worker | Bounded correction required. |
| `SAT2_MENTOR_ACCEPTED` | Mentor | none | Candidate accepted; downstream work remains blocked until a separate `SAT2_TASK_AUTHORIZED` event. |
| `SAT2_TASK_BLOCKED` | Mentor/Worker | none | Task paused pending a stated condition. |
| `SAT2_HUMAN_GATE` | Mentor/Relay | none | User approval is required. |
| `SAT2_RELAY_ALERT` | Relay | none | Infrastructure or protocol incident. |
| `SAT2_TASK_CANCELLED` | Mentor/User | none | Task is terminated; no automatic continuation. |

## 7. Relay state machine

Relay state is separate from evidence maturity.

```text
DORMANT
→ READY
→ DISPATCHED
→ WORKING
→ MENTOR_REVIEW
→ CHANGES_REQUIRED → WORKING
                or → ACCEPTED
                or → BLOCKED
                or → HUMAN_GATE
```

The evidence state remains:

```text
SPECIFIED → IMPLEMENTED → PREFLIGHT_PASSED → FORMAL_RUNNING
→ EVIDENCE_COMPLETE → MENTOR_ACCEPTED → PAPER_INTEGRATED
```

A relay `ACCEPTED` transition does not manufacture evidence maturity. The human-readable Mentor record remains authoritative for allowed and forbidden claims.

## 8. Required SHA binding

- `SAT2_TASK_AUTHORIZED` requires `base_sha`, which must equal the Worker PR base SHA.
- `SAT2_WORKER_CHECKPOINT` requires `candidate_sha`.
- `SAT2_MENTOR_CHANGES_REQUIRED` and `SAT2_MENTOR_ACCEPTED` require `reviewed_sha`.
- `control_head_sha` records the current PR head when the scientific candidate is followed by metadata-only commits.
- Without `control_head_sha`, the bound candidate/reviewed SHA must equal the current PR head.
- With `control_head_sha`, it must equal the current PR head and the scientific SHA must occur in the PR commit set.
- All SHA values are lowercase full 40-character commit IDs. A mismatch blocks routing and creates an alert.

## 9. Execution Capsule

The daemon converts a validated event into a short Execution Capsule. A Capsule contains only:

- role;
- task ID;
- PR;
- exact bound SHA;
- source comment;
- required GitHub app;
- one current action;
- five mandatory controls.

The model is not expected to remember the full protocol. It must re-read the task YAML, source comment, and PR state named in the Capsule.

## 10. Deduplication and editing

The daemon persists:

```text
repository, PR, comment ID, comment updated_at, body hash,
event ID, target role, delivery status, attempt count
```

On the first poll of a newly configured monitor, existing comments are recorded as a baseline and are not dispatched unless `process_existing_events_on_first_poll` is explicitly enabled. A `start_after_comment_id` boundary may also be configured.

Rules:

- duplicate event ID: ignore;
- duplicate event/target delivery: ignore;
- old event received late: block as out of order;
- already processed comment edited: stop and alert;
- expired delivery lease: recover to retry;
- daemon restart: reload queue from SQLite.

## 11. Retries

Default delivery retries are 60 seconds, 300 seconds, and 900 seconds.

Retryable examples:

- target session busy;
- temporary network failure;
- browser tab loading;
- daemon or extension temporary disconnect.

Non-retryable or human-intervention examples:

- ChatGPT login expired;
- required GitHub app cannot be confirmed;
- confirmation/permission dialog visible;
- role not bound;
- PR/SHA/task mismatch;
- untrusted actor;
- protocol/schema error;
- forbidden transition.

## 12. Alerts

The daemon always records incidents in local SQLite. It can also post to a long-lived GitHub Alert Issue when:

- local `github.allow_writes` is true;
- `alert_issue` is configured;
- a write-capable alert token is supplied.

For reliable GitHub email, use a low-permission GitHub App or separate bot token to post and mention the user. A comment posted by the same personal account is not treated as a guaranteed self-notification path.

Recommended bot permissions:

```text
Metadata: read
Contents: read
Pull requests: read
Issues: write
```

It must not have Actions, merge, branch-write, contents-write, or administration permission.

## 13. Human gates

The Relay must never automatically perform or authorize:

- merge or ready-for-review transition;
- workflow dispatch or formal experiment;
- registry, seed, accepted evidence, historical hash, or artifact deletion;
- paper numerical result or claim expansion;
- force push or base-branch change;
- task scope expansion;
- overlapping Worker write ownership.

It emits `SAT2_HUMAN_GATE` and stops the affected task.

## 14. Deployment sequence

1. Merge protocol/tooling through an independent maintenance PR.
2. Install daemon and extension.
3. Keep `enabled: false`, `mode: shadow`.
4. Bind Mentor and one test Worker.
5. Validate Worker → Mentor → Worker → Mentor in dry-run.
6. Enable active mode for one Worker.
7. Enable multiple Workers only after restart recovery, duplicate handling, SHA mismatch blocking, and alert delivery are verified.

Existing in-progress Worker PRs are not retroactively converted. New Relay control starts at the next explicit Mentor authorization boundary.
