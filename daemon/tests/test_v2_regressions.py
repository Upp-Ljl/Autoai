from __future__ import annotations

import os

from sat2_relay.config import load_local_config
from sat2_relay.credentials import CredentialStore, resolve_secret
from sat2_relay.db import RelayDB
from sat2_relay.github import GitHubError
from sat2_relay.models import RepoMonitor
from sat2_relay.protocol import validate_event_document
from sat2_relay.service import RelayService
from test_service import AUTH_BODY, EVENT_BODY, FakeGitHub, REPO_CONFIG, TASK_SPEC


def test_task_spec_defaults_to_repository_config_ref(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    refs = []
    original = gh.get_content_text

    def get_content(repository, path, ref):
        refs.append((path, ref))
        return original(repository, path, ref)

    gh.get_content_text = get_content
    service = RelayService(local_config, db, gh)
    service.poll_once()
    task_reads = [ref for path, ref in refs if path == ".sat2/tasks/WP-B3.yml"]
    # Relay 2.2.2 deliberately revalidates the frozen task contract on later
    # control events. The invariant is therefore the authoritative ref, not a
    # historical assumption that the file is read exactly once.
    assert task_reads
    assert set(task_reads) == {"test-ref"}


def test_task_spec_pr_head_is_explicit_opt_in(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    config = REPO_CONFIG.replace(
        "task_file: .sat2/tasks/WP-B3.yml",
        "task_file: .sat2/tasks/WP-B3.yml\n    task_ref: '@pr-head'",
    )
    refs = []

    def get_content(repository, path, ref):
        refs.append((path, ref))
        return config if path == ".sat2/relay.yml" else TASK_SPEC

    gh.get_content_text = get_content
    service = RelayService(local_config, db, gh)
    service.poll_once()
    assert (".sat2/tasks/WP-B3.yml", "a" * 40) in refs


def test_retryable_task_spec_404_replays_same_comment(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    gh.comments = [gh.comments[0]]
    attempts = {"task": 0}

    def get_content(repository, path, ref):
        if path == ".sat2/relay.yml":
            return REPO_CONFIG
        attempts["task"] += 1
        if attempts["task"] == 1:
            raise GitHubError("GET", "/contents/task", 404, "Not Found", {"ref": ref})
        return TASK_SPEC

    gh.get_content_text = get_content
    service = RelayService(local_config, db, gh)
    first = service.poll_once()
    assert first["retryable"] == 1
    assert first["events"] == 0
    row = db.comment_status("Upp-Ljl/sat2", 32, 99)
    assert row["outcome"] == "retryable_error"

    second = service.poll_once()
    assert second["events"] == 1
    assert second["deliveries"] == 1
    row = db.comment_status("Upp-Ljl/sat2", 32, 99)
    assert row["outcome"] == "processed"
    assert db.get_meta("task_contract:WP-B3:sha256")


def test_v2_ack_requires_parent_event_id():
    raw = {
        "protocol": "sat2-relay/v2",
        "event_id": "ack-1",
        "event_type": "SAT2_WORKER_ACK",
        "repository": "Upp-Ljl/sat2",
        "task_id": "WP-B3",
        "actor_role": "S2",
        "pr_number": 32,
        "control_head_sha": "a" * 40,
        "attempt": 1,
        "timestamp": "2026-08-04T00:00:00+08:00",
    }
    try:
        validate_event_document(raw)
    except Exception as exc:
        assert "parent_event_id" in str(exc)
    else:
        raise AssertionError("v2 ACK without parent_event_id was accepted")


def test_credential_store_precedes_environment(tmp_path, monkeypatch):
    store = CredentialStore(tmp_path / "credentials.bin")
    store.set("github_token", "store-token")
    monkeypatch.setenv("SAT2_GITHUB_TOKEN", "environment-token")
    secret = resolve_secret(store, "github_token", "SAT2_GITHUB_TOKEN")
    assert secret.value == "store-token"
    assert secret.source.startswith("credential_store:")
    if os.name != "nt":
        assert (store.path.stat().st_mode & 0o077) == 0


def test_v1_task_spec_404_is_migrated_to_retryable(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE comments(repository TEXT,pr_number INTEGER,comment_id INTEGER,updated_at TEXT,body_hash TEXT,actor TEXT,processed_at TEXT,outcome TEXT,PRIMARY KEY(repository,pr_number,comment_id));
        CREATE TABLE alerts(id INTEGER PRIMARY KEY AUTOINCREMENT,severity TEXT,code TEXT,task_id TEXT,pr_number INTEGER,detail TEXT,github_comment_url TEXT,created_at TEXT,resolved_at TEXT);
        INSERT INTO comments VALUES('Upp-Ljl/sat2',48,5169171869,'t','h','Upp-Ljl','t','invalid_event');
        INSERT INTO alerts(severity,code,task_id,pr_number,detail,created_at) VALUES('error','PROTOCOL_OR_STATE_INVALID','P0-B-WP-B',48,'Comment 5169171869: GitHub GET /repos/Upp-Ljl/sat2/contents/.sat2/tasks/P0-B-WP-B.yml: 404','t');
        """
    )
    conn.commit()
    conn.close()
    db = RelayDB(path)
    row = db.comment_status("Upp-Ljl/sat2", 48, 5169171869)
    assert row["outcome"] == "retryable_error"


def test_first_poll_respects_explicit_start_boundary(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    config = REPO_CONFIG.replace(
        "process_existing_events_on_first_poll: true",
        "process_existing_events_on_first_poll: false",
    ).replace(
        "task_file: .sat2/tasks/WP-B3.yml",
        "start_after_comment_id: 98\n    task_file: .sat2/tasks/WP-B3.yml",
    )
    gh.get_content_text = lambda repository, path, ref: config if path == ".sat2/relay.yml" else TASK_SPEC
    service = RelayService(local_config, db, gh)
    result = service.poll_once()
    assert result["events"] == 2
    assert result["deliveries"] == 2
    assert db.get_meta("task_contract:WP-B3:sha256")


def test_complete_v2_wire_chain_keeps_ack_compatible_but_not_required(local_config):
    """Legacy v2 ACK remains parseable, while 2.2.2 no longer requires it.

    This test exercises wire compatibility only. The normal no-ACK checkpoint
    path is covered in test_decisions_v22.py.
    """
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    config = REPO_CONFIG.replace("protocol_version: sat2-relay/v1", "protocol_version: sat2-relay/v2")
    gh.get_content_text = lambda repository, path, ref: config if path == ".sat2/relay.yml" else TASK_SPEC
    auth = AUTH_BODY.replace("SAT2_RELAY_EVENT_V1", "SAT2_RELAY_EVENT_V2").replace(
        "protocol: sat2-relay/v1", "protocol: sat2-relay/v2"
    )
    gh.comments = [dict(gh.comments[0], body=auth)]
    service = RelayService(local_config, db, gh)

    first = service.poll_once()
    assert first["events"] == 1 and first["deliveries"] == 1
    assert db.task_state("WP-B3")["state"] == "DISPATCHED"
    delivery = db.lease_next("install-12345678", 60)
    assert delivery and delivery.target_role == "S2"
    assert db.complete_delivery(delivery.id, True, "DELIVERED", None, 3, [1, 2, 3]) == "delivered"

    ack_body = """
<!-- SAT2_RELAY_EVENT_V2 -->
```yaml
protocol: sat2-relay/v2
event_id: WP-B3-ack-1
event_type: SAT2_WORKER_ACK
repository: Upp-Ljl/sat2
task_id: WP-B3
actor_role: S2
pr_number: 32
parent_event_id: WP-B3-auth-1
control_head_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
attempt: 1
timestamp: 2026-08-04T00:01:00+08:00
summary: received
```
"""
    gh.comments.append({
        "id": 101,
        "body": ack_body,
        "created_at": "2026-08-03T16:01:00Z",
        "updated_at": "2026-08-03T16:01:00Z",
        "html_url": "https://example/101",
        "user": {"login": "Upp-Ljl"},
    })
    second = service.poll_once()
    assert second["events"] == 1
    assert db.task_state("WP-B3")["state"] == "DISPATCHED"

    checkpoint_body = """
<!-- SAT2_RELAY_EVENT_V2 -->
```yaml
protocol: sat2-relay/v2
event_id: WP-B3-checkpoint-v2-1
event_type: SAT2_WORKER_CHECKPOINT
repository: Upp-Ljl/sat2
task_id: WP-B3
actor_role: S2
pr_number: 32
parent_event_id: WP-B3-ack-1
candidate_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
control_head_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
attempt: 1
timestamp: 2026-08-04T00:02:00+08:00
summary: source checkpoint
```
"""
    gh.comments.append({
        "id": 102,
        "body": checkpoint_body,
        "created_at": "2026-08-03T16:02:00Z",
        "updated_at": "2026-08-03T16:02:00Z",
        "html_url": "https://example/102",
        "user": {"login": "Upp-Ljl"},
    })
    third = service.poll_once()
    assert third["events"] == 1 and third["deliveries"] == 1
    assert db.task_state("WP-B3")["state"] == "MENTOR_REVIEW"
    mentor_delivery = db.lease_next("install-12345678", 60)
    assert mentor_delivery and mentor_delivery.target_role == "mentor"
    db.complete_delivery(mentor_delivery.id, True, "DELIVERED", None, 3, [1, 2, 3])

    accepted_body = """
<!-- SAT2_RELAY_EVENT_V2 -->
```yaml
protocol: sat2-relay/v2
event_id: WP-B3-accepted-1
event_type: SAT2_MENTOR_ACCEPTED
repository: Upp-Ljl/sat2
task_id: WP-B3
actor_role: mentor
pr_number: 32
parent_event_id: WP-B3-checkpoint-v2-1
reviewed_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
control_head_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
attempt: 1
timestamp: 2026-08-04T00:03:00+08:00
summary: accepted
```
"""
    gh.comments.append({
        "id": 103,
        "body": accepted_body,
        "created_at": "2026-08-03T16:03:00Z",
        "updated_at": "2026-08-03T16:03:00Z",
        "html_url": "https://example/103",
        "user": {"login": "Upp-Ljl"},
    })
    fourth = service.poll_once()
    assert fourth["events"] == 1
    assert db.task_state("WP-B3")["state"] == "COMPLETE"
