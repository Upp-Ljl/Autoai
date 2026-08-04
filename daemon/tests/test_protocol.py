from __future__ import annotations

from sat2_relay.models import EventType, RepoMonitor
from sat2_relay.protocol import build_execution_capsule, extract_event_documents, resolve_target, validate_actor_semantics, validate_event_document, validate_pr_binding


def event_block(event_type="SAT2_WORKER_CHECKPOINT"):
    sha = "a" * 40
    field = "candidate_sha" if event_type == "SAT2_WORKER_CHECKPOINT" else "base_sha"
    return f"""text
<!-- SAT2_RELAY_EVENT_V1 -->
```yaml
protocol: sat2-relay/v1
event_id: WP-B3-001
event_type: {event_type}
repository: Upp-Ljl/sat2
task_id: WP-B3
actor_role: S2
pr_number: 32
{field}: {sha}
attempt: 1
timestamp: 2026-07-26T10:00:00+09:00
```
"""


def test_extract_and_validate():
    docs = extract_event_documents(event_block())
    event = validate_event_document(docs[0])
    assert event.event_type is EventType.WORKER_CHECKPOINT
    assert event.candidate_sha == "a" * 40


def test_invalid_sha_fails_closed():
    body = event_block().replace("a" * 40, "abc")
    try:
        validate_event_document(extract_event_documents(body)[0])
    except Exception:
        pass
    else:
        raise AssertionError("invalid SHA accepted")


def test_route_and_capsule():
    event = validate_event_document(extract_event_documents(event_block())[0])
    monitor = RepoMonitor(pr_number=32, task_id="WP-B3", worker_role="S2")
    assert resolve_target(event, monitor) == "mentor"
    capsule = build_execution_capsule(event, "mentor", "WP-B3-001:mentor", monitor)
    assert "@GitHub" in capsule
    assert "Relay delivery: WP-B3-001:mentor" in capsule
    assert "PR: #32" in capsule


def test_actor_and_pr_binding():
    event = validate_event_document(extract_event_documents(event_block())[0])
    monitor = RepoMonitor(pr_number=32, task_id="WP-B3", worker_role="S2")
    validate_actor_semantics(event, monitor)
    validate_pr_binding(event, {"head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}}, {"a" * 40})


def test_wrong_worker_fails_closed():
    raw = extract_event_documents(event_block())[0]
    raw["actor_role"] = "S3"
    event = validate_event_document(raw)
    monitor = RepoMonitor(pr_number=32, task_id="WP-B3", worker_role="S2")
    try:
        validate_actor_semantics(event, monitor)
    except ValueError:
        pass
    else:
        raise AssertionError("wrong worker accepted")
