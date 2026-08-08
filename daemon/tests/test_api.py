from __future__ import annotations

from fastapi.testclient import TestClient

from sat2_relay.api import create_app
from sat2_relay.db import RelayDB
from sat2_relay.service import RelayService
from test_service import FakeGitHub


def test_api_auth_and_heartbeat(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    app = create_app(local_config, db, service, poll_enabled=False)
    with TestClient(app) as client:
        assert client.get("/api/v1/health").status_code == 401
        headers = {"X-SAT2-Relay-Token": "test-local-token-0123456789abcdef"}
        response = client.get("/api/v1/health", headers=headers)
        assert response.status_code == 200
        heartbeat = client.post("/api/v1/extension/heartbeat", headers=headers, json={
            "installation_id": "install-12345678", "extension_version": "2.2.2", "auto_enabled": True,
            "bindings": {"mentor": "https://chatgpt.com/c/test"}, "active_roles": ["mentor"], "browser": "test"
        })
        assert heartbeat.status_code == 200
        assert db.status_snapshot()["heartbeats"]


def test_diagnostics_export_redacts_secrets_and_delivery_bodies(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    app = create_app(local_config, db, service, poll_enabled=False)
    headers = {"X-SAT2-Relay-Token": "test-local-token-0123456789abcdef"}
    with TestClient(app) as client:
        response = client.get("/api/v2/diagnostics/export", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        encoded = response.text
        assert payload["redaction"]["github_tokens"] == "never included"
        assert "test-local-token-0123456789abcdef" not in encoded
        assert "body" not in (payload["deliveries"][0] if payload["deliveries"] else {})


def test_delivery_endpoint_leases_only_roles_bound_to_requesting_installation(local_config):
    from sat2_relay.models import RelayEvent

    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    app = create_app(local_config, db, service, poll_enabled=False)
    event = RelayEvent.model_validate({
        "protocol": "sat2-relay/v1",
        "event_id": "checkpoint-routing-1",
        "event_type": "SAT2_WORKER_CHECKPOINT",
        "repository": "Upp-Ljl/sat2",
        "task_id": "WP-B3",
        "actor_role": "S2",
        "pr_number": 32,
        "candidate_sha": "a" * 40,
        "attempt": 1,
        "timestamp": "2026-08-04T00:00:00+08:00",
        "source_comment_id": 900,
    })
    assert db.insert_event(event, event.model_dump_json())
    delivery_id = db.enqueue_delivery(event.event_id, "mentor", "mentor capsule", [], False, False)
    assert delivery_id
    headers = {"X-SAT2-Relay-Token": "test-local-token-0123456789abcdef"}
    with TestClient(app) as client:
        worker_hb = {
            "installation_id": "worker-install-123",
            "extension_version": "2.2.2",
            "auto_enabled": True,
            "bindings": {"S3": {"url": "https://chatgpt.com/c/s3", "conversation_key": "c:s3"}},
            "active_roles": ["S3"],
        }
        assert client.post("/api/v2/extension/heartbeat", headers=headers, json=worker_hb).status_code == 200
        worker_next = client.get("/api/v2/deliveries/next?installation_id=worker-install-123", headers=headers)
        assert worker_next.status_code == 200
        assert worker_next.json()["delivery"] is None
        assert db.status_snapshot()["deliveries"][0]["attempt_count"] == 0

        mentor_hb = {
            "installation_id": "mentor-install-123",
            "extension_version": "2.2.2",
            "auto_enabled": True,
            "bindings": {"mentor": {"url": "https://chatgpt.com/c/mentor", "conversation_key": "c:mentor"}},
            "active_roles": ["mentor"],
        }
        assert client.post("/api/v2/extension/heartbeat", headers=headers, json=mentor_hb).status_code == 200
        mentor_next = client.get("/api/v2/deliveries/next?installation_id=mentor-install-123", headers=headers)
        assert mentor_next.status_code == 200
        assert mentor_next.json()["delivery"]["target_role"] == "mentor"
