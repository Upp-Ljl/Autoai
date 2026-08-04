from __future__ import annotations

from dataclasses import replace

from sat2_relay.db import RelayDB
from sat2_relay.decisions import DecisionEngine, DecisionError
from sat2_relay.models import DecisionSubmission, Heartbeat
from sat2_relay.service import RelayService
from test_service import AUTH_BODY, FakeGitHub, REPO_CONFIG


class PublishingGitHub(FakeGitHub):
    def __init__(self):
        super().__init__()
        self.next_comment_id = 1000

    def create_issue_comment(self, repository, issue_number, body):
        self.next_comment_id += 1
        row = {
            "id": self.next_comment_id,
            "body": body,
            "created_at": "2026-08-04T10:00:00Z",
            "updated_at": "2026-08-04T10:00:00Z",
            "html_url": f"https://github.com/{repository}/pull/{issue_number}#issuecomment-{self.next_comment_id}",
            "user": {"login": "Upp-Ljl"},
        }
        self.comments.append(row)
        return row


def configured_runtime(local_config):
    local = replace(local_config, allow_github_writes=True)
    db = RelayDB(local.database_path)
    gh = PublishingGitHub()
    config = REPO_CONFIG.replace("protocol_version: sat2-relay/v1", "protocol_version: sat2-relay/v2").replace(
        "process_existing_events_on_first_poll: true", "process_existing_events_on_first_poll: true"
    )
    gh.get_content_text = lambda repository, path, ref: config if path == ".sat2/relay.yml" else "task_id: WP-B3\n"
    auth = AUTH_BODY.replace("SAT2_RELAY_EVENT_V1", "SAT2_RELAY_EVENT_V2").replace("protocol: sat2-relay/v1", "protocol: sat2-relay/v2")
    gh.comments = [dict(gh.comments[0], body=auth)]
    service = RelayService(local, db, gh)
    service.poll_once()
    return local, db, gh, service


def bind(db, installation_id, role, conversation_key):
    payload = Heartbeat.model_validate({
        "installation_id": installation_id,
        "extension_version": "2.2.0",
        "auto_enabled": True,
        "bindings": {role: {"url": f"https://chatgpt.com/c/{conversation_key.split(':',1)[1]}", "conversation_key": conversation_key, "composer_ready": True}},
        "active_roles": [role],
    })
    db.record_heartbeat(payload.installation_id, payload.extension_version, payload.model_dump_json())


def delivered_for(db, role, installation_id):
    delivery = db.lease_next(installation_id, 60, eligible_roles={role})
    assert delivery is not None
    db.complete_delivery(delivery.id, True, "DELIVERED", None, 8, [5, 10, 20, 30])
    return delivery


def submission(delivery, *, installation, role, conversation, decision, message_hash, summary):
    return DecisionSubmission.model_validate({
        "installation_id": installation,
        "role": role,
        "conversation_key": conversation,
        "delivery_id": delivery.id,
        "delivery_token": delivery.delivery_token,
        "assistant_message_id": f"message-{message_hash[:8]}",
        "assistant_message_hash": message_hash,
        "decision": decision,
        "summary": summary,
    })


def test_worker_checkpoint_is_composed_published_and_routed(local_config):
    _local, db, gh, service = configured_runtime(local_config)
    bind(db, "worker-install-1", "S2", "c:worker")
    delivery = delivered_for(db, "S2", "worker-install-1")
    engine = DecisionEngine(service, db)
    result = engine.submit(submission(
        delivery,
        installation="worker-install-1",
        role="S2",
        conversation="c:worker",
        decision="WORKER_CHECKPOINT",
        message_hash="1" * 64,
        summary="Source repair complete; request Mentor review.",
    ))
    assert result["outbox"]["status"] == "published"
    body = gh.comments[-1]["body"]
    assert "SAT2_RELAY_AUTO_EVENT" in body
    assert "event_type: SAT2_WORKER_CHECKPOINT" in body
    assert f"candidate_sha: {'a' * 40}" in body
    assert "actor_role: S2" in body
    assert "target_role: mentor" in body
    service.poll_once()
    assert db.task_state("WP-B3")["state"] == "MENTOR_REVIEW"
    bind(db, "mentor-install-1", "mentor", "c:mentor")
    mentor_delivery = delivered_for(db, "mentor", "mentor-install-1")
    assert mentor_delivery.delivery_token
    assert "SAT2_RELAY_DECISION" in mentor_delivery.body


def test_wrong_token_and_wrong_role_are_rejected(local_config):
    _local, db, _gh, service = configured_runtime(local_config)
    bind(db, "worker-install-1", "S2", "c:worker")
    delivery = delivered_for(db, "S2", "worker-install-1")
    engine = DecisionEngine(service, db)
    payload = submission(
        delivery,
        installation="worker-install-1",
        role="S2",
        conversation="c:worker",
        decision="WORKER_CHECKPOINT",
        message_hash="2" * 64,
        summary="checkpoint",
    )
    payload.delivery_token = "x" * 24
    try:
        engine.submit(payload)
    except DecisionError as exc:
        assert exc.code == "DELIVERY_TOKEN_MISMATCH"
    else:
        raise AssertionError("wrong token was accepted")


def test_same_assistant_message_is_idempotent(local_config):
    _local, db, gh, service = configured_runtime(local_config)
    bind(db, "worker-install-1", "S2", "c:worker")
    delivery = delivered_for(db, "S2", "worker-install-1")
    engine = DecisionEngine(service, db)
    payload = submission(
        delivery,
        installation="worker-install-1",
        role="S2",
        conversation="c:worker",
        decision="WORKER_CHECKPOINT",
        message_hash="3" * 64,
        summary="checkpoint",
    )
    first = engine.submit(payload)
    second = engine.submit(payload)
    assert first["outbox"]["event_id"] == second["outbox"]["event_id"]
    assert len([row for row in gh.comments if "SAT2_RELAY_AUTO_EVENT" in row["body"]]) == 1


def test_mentor_acceptance_waits_for_human_confirmation(local_config):
    _local, db, _gh, service = configured_runtime(local_config)
    bind(db, "worker-install-1", "S2", "c:worker")
    worker_delivery = delivered_for(db, "S2", "worker-install-1")
    engine = DecisionEngine(service, db)
    engine.submit(submission(
        worker_delivery,
        installation="worker-install-1", role="S2", conversation="c:worker",
        decision="WORKER_CHECKPOINT", message_hash="4" * 64, summary="checkpoint"
    ))
    service.poll_once()
    bind(db, "mentor-install-1", "mentor", "c:mentor")
    mentor_delivery = delivered_for(db, "mentor", "mentor-install-1")
    result = engine.submit(submission(
        mentor_delivery,
        installation="mentor-install-1", role="mentor", conversation="c:mentor",
        decision="MENTOR_ACCEPTED", message_hash="5" * 64, summary="accepted source only"
    ))
    assert result["waiting_for_human"] is True
    assert result["outbox"]["status"] == "waiting_for_human"
    confirmed = engine.confirm(int(result["outbox"]["id"]))
    assert confirmed["status"] == "published"
    service.poll_once()
    assert db.task_state("WP-B3")["state"] == "COMPLETE"
