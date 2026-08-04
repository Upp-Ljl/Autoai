from __future__ import annotations

from sat2_relay.db import RelayDB
from sat2_relay.models import RelayEvent


def make_event():
    return RelayEvent.model_validate({
        "protocol": "sat2-relay/v1", "event_id": "E-1", "event_type": "SAT2_WORKER_CHECKPOINT",
        "repository": "Upp-Ljl/sat2", "task_id": "WP-B3", "actor_role": "S2", "pr_number": 32,
        "candidate_sha": "a" * 40, "attempt": 1, "timestamp": "2026-07-26T10:00:00+09:00",
        "source_comment_id": 9
    })


def test_persistent_delivery_and_dedup(tmp_path):
    path = tmp_path / "state.sqlite3"
    db = RelayDB(path)
    event = make_event()
    assert db.insert_event(event, event.model_dump_json())
    assert not db.insert_event(event, event.model_dump_json())
    delivery_id = db.enqueue_delivery(event.event_id, "mentor", "hello", ["GitHub"], True, False)
    assert delivery_id
    assert db.enqueue_delivery(event.event_id, "mentor", "hello", ["GitHub"], True, False) is None
    leased = db.lease_next("installation-123", 60)
    assert leased and leased.id == delivery_id
    assert db.complete_delivery(delivery_id, False, "SESSION_BUSY", "busy", 3, [1, 2, 3]) == "retry"
    db2 = RelayDB(path)
    snapshot = db2.status_snapshot()
    assert snapshot["deliveries"][0]["status"] == "retry"


def test_dry_run_approval(tmp_path):
    db = RelayDB(tmp_path / "state.sqlite3")
    event = make_event()
    db.insert_event(event, event.model_dump_json())
    delivery_id = db.enqueue_delivery(event.event_id, "mentor", "hello", [], False, True)
    assert db.lease_next("installation-123", 60) is None
    assert db.approve_delivery(delivery_id)
    assert db.lease_next("installation-123", 60)


def test_cancel_held_delivery(tmp_path):
    db = RelayDB(tmp_path / "state.sqlite3")
    event = make_event()
    db.insert_event(event, event.model_dump_json())
    delivery_id = db.enqueue_delivery(event.event_id, "mentor", "hello", [], False, True)
    assert delivery_id
    assert db.cancel_delivery(delivery_id)
    assert db.lease_next("installation-123", 60) is None
    assert db.status_snapshot()["deliveries"][0]["status"] == "cancelled"


def test_resolve_alerts_filters_exactly(tmp_path):
    db = RelayDB(tmp_path / "state.sqlite3")
    first = db.add_alert("error", "TASK_SPEC_UNAVAILABLE", "a", "T1", 48)
    second = db.add_alert("error", "TASK_SPEC_UNAVAILABLE", "b", "T2", 49)
    assert db.resolve_alerts(code="TASK_SPEC_UNAVAILABLE", task_id="T1", pr_number=48) == 1
    rows = {row["id"]: row for row in db.status_snapshot()["alerts"]}
    assert rows[first]["resolved_at"] is not None
    assert rows[second]["resolved_at"] is None


def test_role_aware_lease_does_not_consume_other_role(tmp_path):
    db = RelayDB(tmp_path / "state.sqlite3")
    event = make_event()
    assert db.insert_event(event, event.model_dump_json())
    delivery_id = db.enqueue_delivery(event.event_id, "mentor", "hello", [], False, False)
    assert delivery_id

    # A worker-only browser installation must not consume a Mentor delivery.
    assert db.lease_next("worker-install", 60, eligible_roles={"S3"}) is None
    row = db.status_snapshot()["deliveries"][0]
    assert row["status"] == "pending"
    assert row["attempt_count"] == 0

    leased = db.lease_next("mentor-install", 60, eligible_roles={"mentor"})
    assert leased and leased.id == delivery_id


def test_heartbeat_builds_persistent_role_endpoint_registry(tmp_path):
    import json

    db = RelayDB(tmp_path / "state.sqlite3")
    payload = {
        "installation_id": "install-mentor-123",
        "extension_version": "2.0.1",
        "auto_enabled": True,
        "bindings": {
            "mentor": {
                "url": "https://chatgpt.com/c/mentor-1",
                "conversation_key": "c:mentor-1",
                "tab_id": 7,
                "composer_ready": True,
            }
        },
        "active_roles": ["mentor"],
    }
    db.record_heartbeat("install-mentor-123", "2.0.1", json.dumps(payload))
    assert db.bound_roles_for_installation("install-mentor-123", 90) == {"mentor"}
    endpoints = db.fresh_role_endpoints(90)
    assert len(endpoints) == 1
    assert endpoints[0]["role"] == "mentor"
    assert endpoints[0]["conversation_key"] == "c:mentor-1"
    assert endpoints[0]["active"] == 1


def test_failed_role_not_bound_delivery_is_requeued_on_upgrade(tmp_path):
    path = tmp_path / "state.sqlite3"
    db = RelayDB(path)
    event = make_event()
    assert db.insert_event(event, event.model_dump_json())
    delivery_id = db.enqueue_delivery(event.event_id, "mentor", "hello", [], False, False)
    assert delivery_id
    assert db.lease_next("install-without-mentor", 60)
    assert db.complete_delivery(
        delivery_id,
        False,
        "ROLE_NOT_BOUND",
        "mentor was not bound",
        8,
        [5, 10, 20, 30],
        retryable=False,
    ) == "failed"

    migrated = RelayDB(path)
    row = migrated.status_snapshot()["deliveries"][0]
    assert row["status"] == "retry"
    assert row["attempt_count"] == 0
