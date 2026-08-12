from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .models import EventType, RelayEvent, RepoMonitor, RouteSignalMode


# A delivered Capsule may retain its delivery marker immediately before the
# event YAML when a Session quotes it in a response. Treat that marker as
# transport metadata, not as a reason to discard an otherwise valid event.
_SENTINEL_RE = re.compile(r"<!--\s*SAT2_RELAY_EVENT_V(?:1|2)\s*-->(?:\s|<!--\s*SAT2_RELAY_DELIVERY:\s*[^>]*-->)*```(?:yaml|yml)\s*(.*?)```", re.I | re.S)
_FENCE_RE = re.compile(r"```sat2-relay\s*(.*?)```", re.I | re.S)


def schema_path() -> Path:
    return Path(__file__).with_name("relay-event-v2.schema.json")


def load_schema() -> dict[str, Any]:
    return json.loads(schema_path().read_text(encoding="utf-8"))


def extract_event_documents(body: str) -> list[dict[str, Any]]:
    blocks = _SENTINEL_RE.findall(body) + _FENCE_RE.findall(body)
    docs: list[dict[str, Any]] = []
    for block in blocks:
        raw = yaml.safe_load(block)
        if not isinstance(raw, dict):
            raise ValueError("relay event block must be a mapping")
        docs.append(raw)
    return docs


def _json_compatible(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_compatible(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_compatible(v) for v in value]
    return value


def validate_event_document(raw: dict[str, Any]) -> RelayEvent:
    normalized = _json_compatible(raw)
    jsonschema.validate(normalized, load_schema())
    return RelayEvent.model_validate(normalized)


def validate_actor_semantics(event: RelayEvent, monitor: RepoMonitor) -> None:
    worker_events = {EventType.WORKER_ACK, EventType.WORKER_CHECKPOINT}
    mentor_events = {EventType.TASK_AUTHORIZED, EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED}
    if event.event_type in worker_events and event.actor_role != monitor.worker_role:
        raise ValueError(f"worker event actor {event.actor_role} does not match monitor worker {monitor.worker_role}")
    if event.event_type in mentor_events and event.actor_role not in {monitor.mentor_role, "user"}:
        raise ValueError(
            f"mentor event actor {event.actor_role} does not match monitor mentor {monitor.mentor_role}"
        )
    if event.event_type is EventType.TASK_BLOCKED and event.actor_role not in {
        monitor.mentor_role,
        monitor.worker_role,
    }:
        raise ValueError(
            f"blocked event actor {event.actor_role} is outside route roles {monitor.mentor_role}/{monitor.worker_role}"
        )
    if event.event_type is EventType.RELAY_ALERT and event.actor_role != "relay":
        raise ValueError("SAT2_RELAY_ALERT must use actor_role relay")
    if event.target_role:
        expected = resolve_target(event, monitor)
        if expected and event.target_role != expected:
            raise ValueError(f"target_role {event.target_role} conflicts with expected target {expected}")


def validate_pr_binding(event: RelayEvent, pr: dict[str, Any], pr_commit_shas: set[str]) -> None:
    head_sha = str((pr.get("head") or {}).get("sha") or "")
    base_sha = str((pr.get("base") or {}).get("sha") or "")
    if event.control_head_sha and event.control_head_sha != head_sha:
        raise ValueError(f"control_head_sha mismatch: event {event.control_head_sha}, PR {head_sha}")
    if event.event_type is EventType.TASK_AUTHORIZED and event.base_sha != base_sha:
        raise ValueError(f"base_sha mismatch: event {event.base_sha}, PR base {base_sha}")
    bound = event.candidate_sha or event.reviewed_sha or event.authorized_sha
    if bound:
        if event.event_type in {EventType.WORKER_CHECKPOINT, EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED}:
            if bound != head_sha:
                raise ValueError(f"bound SHA mismatch: event {bound}, current PR head {head_sha}")
        elif event.control_head_sha:
            if bound not in pr_commit_shas and bound not in {head_sha, base_sha}:
                raise ValueError(f"bound scientific SHA {bound} is not in the PR commit set")
        elif bound != head_sha:
            raise ValueError(f"bound SHA mismatch: event {bound}, PR head {head_sha}")


def resolve_target(event: RelayEvent, monitor: RepoMonitor | None) -> str | None:
    if event.event_type in {EventType.WORKER_ACK, EventType.WORKER_CHECKPOINT}:
        return monitor.mentor_role if monitor else "mentor"
    if event.event_type is EventType.TASK_BLOCKED:
        if monitor and event.actor_role == monitor.worker_role:
            return monitor.mentor_role
        return None
    if event.event_type in {EventType.TASK_AUTHORIZED, EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED}:
        return monitor.worker_role if monitor else event.next_actor
    return None


def transition_name(event: RelayEvent) -> str:
    return {
        EventType.TASK_AUTHORIZED: "DISPATCHED",
        EventType.WORKER_ACK: "DISPATCHED",  # legacy informational event only
        EventType.WORKER_CHECKPOINT: "MENTOR_REVIEW",
        EventType.MENTOR_CHANGES_REQUIRED: "DISPATCHED",
        EventType.MENTOR_ACCEPTED: "COMPLETE",
        EventType.TASK_BLOCKED: "BLOCKED",
        EventType.HUMAN_GATE: "HUMAN_GATE",
        EventType.RELAY_ALERT: "BLOCKED",
        EventType.TASK_CANCELLED: "CANCELLED",
    }[event.event_type]


_ALLOWED_PREVIOUS = {
    EventType.TASK_AUTHORIZED: {None, "READY", "DORMANT"},
    EventType.WORKER_ACK: {"DISPATCHED"},
    EventType.WORKER_CHECKPOINT: {"DISPATCHED", "WORKING"},  # WORKING retained for v2.0 migration
    EventType.MENTOR_CHANGES_REQUIRED: {"MENTOR_REVIEW"},
    EventType.MENTOR_ACCEPTED: {"MENTOR_REVIEW"},
    EventType.TASK_BLOCKED: {"DISPATCHED", "WORKING", "MENTOR_REVIEW"},
    EventType.HUMAN_GATE: {None, "DISPATCHED", "WORKING", "MENTOR_REVIEW"},
    EventType.RELAY_ALERT: {None, "DISPATCHED", "WORKING", "MENTOR_REVIEW", "BLOCKED"},
    EventType.TASK_CANCELLED: {None, "DISPATCHED", "WORKING", "MENTOR_REVIEW", "BLOCKED", "HUMAN_GATE"},
}


def validate_transition(event: RelayEvent, previous_state: str | None, previous_event_id: str | None = None) -> None:
    allowed = _ALLOWED_PREVIOUS[event.event_type]
    if previous_state not in allowed:
        raise ValueError(f"out-of-order transition: {previous_state!r} -> {event.event_type.value}")
    if event.protocol == "sat2-relay/v2" and event.parent_event_id and previous_event_id and event.parent_event_id != previous_event_id:
        raise ValueError(f"parent_event_id mismatch: event {event.parent_event_id}, expected {previous_event_id}")


def delivery_marker(event: RelayEvent, target_role: str) -> str:
    return f"{event.event_id}:{target_role}"


def _decision_options(event: RelayEvent, target_role: str, monitor: RepoMonitor | None = None) -> list[str]:
    mentor_role = monitor.mentor_role if monitor else "mentor"
    if target_role == mentor_role:
        if event.event_type is EventType.WORKER_ACK:
            return []
        return ["MENTOR_CHANGES_REQUIRED", "MENTOR_ACCEPTED", "TASK_BLOCKED"]
    if event.event_type in {EventType.TASK_AUTHORIZED, EventType.MENTOR_CHANGES_REQUIRED}:
        # WORKER_ACK remains parseable for backward compatibility but is not
        # part of the normal document-driven control path. Delivery confirmation
        # already proves that the Worker received the Capsule.
        return ["WORKER_CHECKPOINT", "TASK_BLOCKED"]
    return []


def _decision_template(
    event: RelayEvent,
    target_role: str,
    delivery_token: str,
    monitor: RepoMonitor | None = None,
) -> str:
    options = _decision_options(event, target_role, monitor)
    if not options:
        return "This Capsule is informational. Do not emit a Relay Decision unless a later task Capsule requests one."
    mentor_role = monitor.mentor_role if monitor else "mentor"
    primary = "WORKER_CHECKPOINT" if target_role != mentor_role else "MENTOR_CHANGES_REQUIRED"
    return f"""When the current work or review reaches a control decision, finish the assistant response with exactly one visible marker and JSON object:

SAT2_RELAY_DECISION
```json
{{"delivery_token":"{delivery_token}","decision":"{primary}","summary":"Describe only the scientific or source-level decision."}}
```

Allowed decision values for this Capsule: {", ".join(options)}.
Do not write Relay YAML, task IDs, PR numbers, SHA values, actor roles, target roles, parent IDs, timestamps, or event IDs into the Decision JSON. The local Relay generates and validates transport fields."""


def _contract_lines(task_spec: dict[str, Any] | None) -> tuple[str, str, str, str]:
    if not task_spec:
        return "[]", "[]", "[]", "[]"
    purpose = task_spec.get("purpose") or task_spec.get("objective") or []
    acceptance = task_spec.get("acceptance") or task_spec.get("acceptance_criteria") or []
    required_reading = task_spec.get("required_reading") or task_spec.get("required_documents") or []
    human_gates = task_spec.get("human_gates") or []
    return (
        json.dumps(purpose, ensure_ascii=False),
        json.dumps(acceptance, ensure_ascii=False),
        json.dumps(required_reading, ensure_ascii=False),
        json.dumps(human_gates, ensure_ascii=False),
    )


def _progress_contract(monitor: RepoMonitor | None, target_role: str) -> str:
    if not monitor or monitor.signal_mode is RouteSignalMode.COMMENT:
        return ""
    mode = "SHADOW ONLY — keep emitting the normal Decision; Relay will compare but not route from progress." if monitor.signal_mode is RouteSignalMode.PROGRESS_SHADOW else "ACTIVE — the progress document plus this Session-bound Decision is the routing authority; no control comment is required."
    actor = "Mentor" if target_role == monitor.mentor_role else "Worker"
    return f"""
PARALLEL ROUTE PROGRESS CONTRACT:
- Route: {monitor.route_id}
- Route role: {actor} ({target_role})
- Mentor role: {monitor.mentor_role}
- Worker role: {monitor.worker_role}
- Progress file: {monitor.progress_file}
- Progress ref: {monitor.progress_ref}
- Signal mode: {monitor.signal_mode.value} ({mode})
- Before emitting the final SAT2_RELAY_DECISION, update the route progress document on the exact progress ref in the same logical handoff as the scientific/task output.
- Increment handoff_sequence by exactly 1 and set parent_sequence to the previous value. Never skip a sequence.
- Set updated_by to exactly {target_role}, event_type to the same semantic decision, current_task/task_id to this task, pr_number to this PR, control_head_sha to the freshly read scientific PR head, and task_contract_sha256 to the frozen contract hash.
- WORKER_CHECKPOINT must set candidate_sha to the freshly read scientific PR head.
- MENTOR_CHANGES_REQUIRED or MENTOR_ACCEPTED must set reviewed_sha to the checkpoint SHA being reviewed; the scientific PR head must still equal that SHA.
- MENTOR_ACCEPTED must either declare a route-local next_task inside the configured task root, or set route_status: COMPLETE with next_task: null.
- TASK_BLOCKED must set route_status: BLOCKED and does not authorize the Relay to guess a recovery action.
- Never modify another route's progress ref, progress file, task root, or Session binding.
"""


def build_execution_capsule(
    event: RelayEvent,
    target_role: str,
    marker: str,
    monitor: RepoMonitor | None,
    delivery_token: str = "legacy-delivery-token-0000",
    task_spec: dict[str, Any] | None = None,
    task_spec_sha256: str | None = None,
) -> str:
    sha = event.candidate_sha or event.reviewed_sha or event.authorized_sha or event.base_sha or "not supplied"
    required_apps = ", ".join(monitor.required_apps if monitor else ["GitHub"])
    source_url = event.source_comment_url or (
        f"progress://{monitor.route_id}/{monitor.progress_file}" if monitor and monitor.route_id else f"GitHub PR #{event.pr_number}"
    )
    task_ref = monitor.task_ref if monitor else "@config"
    action = {
        EventType.TASK_AUTHORIZED: "The Mentor-authored task specification is complete and executable. Read it at the exact task reference, verify the current PR/SHA, then perform the bounded task without waiting for another authorization message.",
        EventType.WORKER_ACK: "Worker receipt was recorded. This legacy informational message requires no response.",
        EventType.WORKER_CHECKPOINT: "Read the Worker checkpoint, the frozen task contract, the exact current PR diff, and independently decide whether every acceptance criterion is satisfied.",
        EventType.MENTOR_CHANGES_REQUIRED: "Read the Mentor findings and the unchanged frozen task contract, repair only the stated blockers, then produce a new Worker checkpoint.",
        EventType.MENTOR_ACCEPTED: "The Mentor accepted the candidate against the frozen task contract. This task is complete; Relay will discover any dependent executable task document automatically.",
    }.get(event.event_type, "Read the source event and act only within the SAT2 control protocol.")
    summary = event.summary or "No additional summary supplied; the task specification, PR, and exact SHA are authoritative."
    decision_template = _decision_template(event, target_role, delivery_token, monitor)
    purpose, acceptance, required_reading, human_gates = _contract_lines(task_spec)
    progress_contract = _progress_contract(monitor, target_role)
    return f"""@GitHub
SAT2 Guided Execution Capsule 2.2

Relay delivery: {marker}
Delivery ID token: {delivery_token}
Protocol: {event.protocol}
Event: {event.event_type.value}
Event ID: {event.event_id}
Parent event: {event.parent_event_id or 'none'}
Correlation ID: {event.correlation_id or event.event_id}
Role: {target_role}
Repository: {event.repository}
Task: {event.task_id}
Task specification: {monitor.task_file if monitor else 'not supplied'}
Task reference: {task_ref}
Task contract SHA-256: {task_spec_sha256 or event.task_spec_sha256 or 'not supplied'}
Task status: {str((task_spec or {}).get('status') or 'not supplied')}
PR: #{event.pr_number}
Bound SHA: {sha}
Control head SHA: {event.control_head_sha or 'not supplied'}
Source: {source_url}
Required app capability: {required_apps}
Allowed paths: {json.dumps(monitor.allowed_paths if monitor else [], ensure_ascii=False)}
Forbidden paths: {json.dumps(monitor.forbidden_paths if monitor else [], ensure_ascii=False)}
Task purpose/objective: {purpose}
Acceptance criteria: {acceptance}
Required reading declared by task: {required_reading}
Human gates declared by task: {human_gates}

Current instruction:
{action}

Event summary:
{summary}

Mandatory controls:
1. GitHub current state, the frozen task contract and exact SHA are authoritative; do not rely on chat memory.
2. Read every document required by the task specification before making a scientific/source decision. The acceptance criteria above are a transport snapshot, not a substitute for reading the task file.
3. A Worker checkpoint means the Worker asserts that the current candidate satisfies every task acceptance criterion that is applicable at this stage. Mentor must independently verify those criteria before accepting.
4. Do not merge, mark ready, dispatch workflows, run qualification/formal experiments, change registry/seeds/evidence/paper, force-push, retarget the base, or expand scope unless the task document explicitly places that action outside a human gate.
5. Session output contains business/scientific judgment only. The extension and local Relay own routing, SHA/parent binding and transport-event publication.
6. The delivery token is scoped to this Capsule, role and conversation. Never reuse a token from another response.
7. If the task contract, PR head, role binding, route progress contract or required evidence is inconsistent, emit TASK_BLOCKED rather than guessing or silently weakening an acceptance criterion.
{progress_contract}
{decision_template}
"""
