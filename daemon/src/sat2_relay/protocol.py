from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .models import EventType, RelayEvent, RepoMonitor


_SENTINEL_RE = re.compile(r"<!--\s*SAT2_RELAY_EVENT_V(?:1|2)\s*-->\s*```(?:yaml|yml)\s*(.*?)```", re.I | re.S)
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
    if event.event_type in mentor_events and event.actor_role not in {"mentor", "user"}:
        raise ValueError(f"mentor event cannot be emitted by role {event.actor_role}")
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
    if event.event_type in {EventType.WORKER_ACK, EventType.WORKER_CHECKPOINT, EventType.TASK_BLOCKED}:
        return "mentor"
    if event.event_type in {EventType.TASK_AUTHORIZED, EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED}:
        return monitor.worker_role if monitor else event.next_actor
    return None


def transition_name(event: RelayEvent) -> str:
    return {
        EventType.TASK_AUTHORIZED: "DISPATCHED",
        EventType.WORKER_ACK: "DISPATCHED",  # informational event; work remains dispatched
        EventType.WORKER_CHECKPOINT: "MENTOR_REVIEW",
        EventType.MENTOR_CHANGES_REQUIRED: "DISPATCHED",
        EventType.MENTOR_ACCEPTED: "COMPLETE",
        EventType.TASK_BLOCKED: "BLOCKED",
        EventType.HUMAN_GATE: "HUMAN_GATE",
        EventType.RELAY_ALERT: "BLOCKED",
        EventType.TASK_CANCELLED: "CANCELLED",
    }[event.event_type]


_ALLOWED_PREVIOUS = {
    EventType.TASK_AUTHORIZED: {None, "READY", "DORMANT", "BLOCKED", "CANCELLED", "COMPLETE", "ACCEPTED"},
    EventType.WORKER_ACK: {"DISPATCHED"},
    EventType.WORKER_CHECKPOINT: {"DISPATCHED", "WORKING"},  # WORKING retained for v2.0 migration
    EventType.MENTOR_CHANGES_REQUIRED: {"MENTOR_REVIEW"},
    EventType.MENTOR_ACCEPTED: {"MENTOR_REVIEW", "HUMAN_GATE"},
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


def _decision_options(event: RelayEvent, target_role: str) -> list[str]:
    if target_role == "mentor":
        if event.event_type is EventType.WORKER_ACK:
            return []
        return ["MENTOR_CHANGES_REQUIRED", "MENTOR_ACCEPTED", "TASK_BLOCKED"]
    if event.event_type in {EventType.TASK_AUTHORIZED, EventType.MENTOR_CHANGES_REQUIRED}:
        return ["WORKER_ACK", "WORKER_CHECKPOINT", "TASK_BLOCKED"]
    return []


def _decision_template(event: RelayEvent, target_role: str, delivery_token: str) -> str:
    options = _decision_options(event, target_role)
    if not options:
        return "This Capsule is informational. Do not emit a Relay Decision unless a later task Capsule requests one."
    primary = "WORKER_CHECKPOINT" if target_role != "mentor" else "MENTOR_CHANGES_REQUIRED"
    return f"""When the current work or review reaches a control decision, finish the assistant response with exactly one visible marker and JSON object:

SAT2_RELAY_DECISION
```json
{{"delivery_token":"{delivery_token}","decision":"{primary}","summary":"Describe only the scientific or source-level decision."}}
```

Allowed decision values for this Capsule: {", ".join(options)}.
Do not write Relay YAML, task IDs, PR numbers, SHA values, actor roles, target roles, parent IDs, timestamps, or event IDs. The local Relay generates and validates all control fields."""


def build_execution_capsule(
    event: RelayEvent,
    target_role: str,
    marker: str,
    monitor: RepoMonitor | None,
    delivery_token: str = "legacy-delivery-token-0000",
) -> str:
    sha = event.candidate_sha or event.reviewed_sha or event.authorized_sha or event.base_sha or "not supplied"
    required_apps = ", ".join(monitor.required_apps if monitor else ["GitHub"])
    source_url = event.source_comment_url or f"GitHub PR #{event.pr_number}"
    task_ref = monitor.task_ref if monitor else "@config"
    action = {
        EventType.TASK_AUTHORIZED: "Read the task specification, current PR, exact SHA and required documents. Perform only the authorized source work.",
        EventType.WORKER_ACK: "Worker receipt was recorded. This message is informational for Mentor; no response event is required.",
        EventType.WORKER_CHECKPOINT: "Read the Worker checkpoint, task specification, exact current PR diff, and complete an independent Mentor review.",
        EventType.MENTOR_CHANGES_REQUIRED: "Read the Mentor findings, repair only the authorized blockers, and produce a new Worker checkpoint when ready.",
        EventType.MENTOR_ACCEPTED: "Read the acceptance record. Do not start a dependent task until a separate authorization Capsule arrives.",
    }.get(event.event_type, "Read the source event and act only within the SAT2 control protocol.")
    summary = event.summary or "No additional summary supplied; the task specification, PR, and exact SHA are authoritative."
    decision_template = _decision_template(event, target_role, delivery_token)
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
PR: #{event.pr_number}
Bound SHA: {sha}
Control head SHA: {event.control_head_sha or 'not supplied'}
Source: {source_url}
Required app capability: {required_apps}
Allowed paths: {json.dumps(monitor.allowed_paths if monitor else [], ensure_ascii=False)}
Forbidden paths: {json.dumps(monitor.forbidden_paths if monitor else [], ensure_ascii=False)}

Current instruction:
{action}

Event summary:
{summary}

Mandatory controls:
1. GitHub current state, the task specification and exact SHA are authoritative; do not rely on chat memory.
2. Read all required documents named by the task specification before making a scientific or source decision.
3. Do not merge, mark ready, dispatch workflows, run qualification/formal experiments, change registry/seeds/evidence/paper, force-push, retarget the base, or expand scope without an explicit current human gate.
4. Session output contains business judgment only. The extension and local Relay own message routing and control-event publication.
5. The delivery token is scoped to this Capsule, role and conversation. Never reuse a token from another response.

{decision_template}
"""
