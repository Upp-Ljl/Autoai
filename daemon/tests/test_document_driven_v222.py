from __future__ import annotations

from dataclasses import replace

from sat2_relay.db import RelayDB
from sat2_relay.service import RelayService
from test_service import FakeGitHub, REPO_CONFIG, TASK_SPEC


class PublishingGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.comments = []
        self.next_comment_id = 2000

    def create_issue_comment(self, repository, issue_number, body):
        self.next_comment_id += 1
        row = {
            "id": self.next_comment_id,
            "body": body,
            "created_at": "2026-08-08T08:00:00Z",
            "updated_at": "2026-08-08T08:00:00Z",
            "html_url": f"https://github.com/{repository}/pull/{issue_number}#issuecomment-{self.next_comment_id}",
            "user": {"login": "Upp-Ljl"},
        }
        self.comments.append(row)
        return row


def runtime(local_config):
    local = replace(local_config, allow_github_writes=True)
    db = RelayDB(local.database_path)
    gh = PublishingGitHub()
    config = REPO_CONFIG.replace("protocol_version: sat2-relay/v1", "protocol_version: sat2-relay/v2")
    gh.get_content_text = lambda repository, path, ref: config if path == ".sat2/relay.yml" else TASK_SPEC
    service = RelayService(local, db, gh)
    return local, db, gh, service


def test_complete_mentor_document_auto_dispatches_without_human_authorization(local_config):
    _local, db, gh, service = runtime(local_config)
    result = service.poll_once()
    assert result["auto_dispatched"] == 1
    assert db.task_state("WP-B3")["state"] == "DISPATCHED"
    assert db.get_meta("task_contract:WP-B3:sha256")
    roots = [row for row in gh.comments if "SAT2_TASK_AUTHORIZED" in row["body"]]
    assert len(roots) == 1
    delivery = db.lease_next("worker-install", 60, eligible_roles={"S2"})
    assert delivery is not None and delivery.target_role == "S2"
    assert "Task contract SHA-256:" in delivery.body
    assert "Acceptance criteria:" in delivery.body
    assert "without waiting for another authorization" in delivery.body


def test_existing_control_history_prevents_second_root(local_config):
    _local, db, gh, service = runtime(local_config)
    gh.comments = [{
        "id": 1999,
        "body": "<!-- SAT2_RELAY_EVENT_V2 -->\n```yaml\nmalformed: true\n```",
        "created_at": "2026-08-08T07:00:00Z",
        "updated_at": "2026-08-08T07:00:00Z",
        "html_url": "https://example/history",
        "user": {"login": "Upp-Ljl"},
    }]
    result = service.poll_once()
    assert result["auto_dispatched"] == 0
    assert not [row for row in gh.comments if "SAT2_RELAY_AUTO_EVENT" in row["body"]]
    assert db.task_state("WP-B3") is None


def test_task_contract_change_is_rejected_after_dispatch(local_config):
    _local, db, gh, service = runtime(local_config)
    service.poll_once()
    assert db.get_meta("task_contract:WP-B3:sha256")
    changed = TASK_SPEC.replace(
        "source changes satisfy the frozen WP-B3 contract",
        "a silently changed acceptance criterion",
    )
    config = REPO_CONFIG.replace("protocol_version: sat2-relay/v1", "protocol_version: sat2-relay/v2")
    gh.get_content_text = lambda repository, path, ref: config if path == ".sat2/relay.yml" else changed
    # The root is already accepted; validating a later task event must see the
    # frozen contract mismatch. Doctor should also expose the current/frozen hashes.
    doctor = service.doctor(deep=True)
    task_checks = [row for row in doctor["checks"] if row["name"] == "monitor:WP-B3:task_contract"]
    assert task_checks
    assert task_checks[0]["detail"]["frozen_contract_sha256"] != task_checks[0]["detail"]["sha256"]
