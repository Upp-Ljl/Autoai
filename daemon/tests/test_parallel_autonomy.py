from __future__ import annotations

import hashlib
import json

import pytest

from sat2_relay.autonomy import ProgressDocument
from sat2_relay.db import RelayDB
from sat2_relay.decisions import DecisionEngine, DecisionError
from sat2_relay.models import DecisionSubmission, RepoMonitor, RepoRelayConfig
from sat2_relay.protocol import resolve_target
from sat2_relay.service import RelayService


HEAD1 = "a" * 40
HEAD2 = "c" * 40
BASE = "b" * 40

TASK1 = """
task_id: V-01
title: Vision route task 1
status: ACTIVE
repository: Upp-Ljl/sat2
pr_number: 101
worker_role: S2
mentor_role: S1
route_id: vision
base_sha: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
purpose:
  - execute vision route task 1
allowed_paths:
  - vision/**
forbidden_paths:
  - paper/**
acceptance:
  - bounded vision result is complete
human_gates: []
""".lstrip()

TASK2 = TASK1.replace("V-01", "V-02").replace("task 1", "task 2")

AGENT_TASK = """
task_id: A-01
title: Agent route task 1
status: ACTIVE
repository: Upp-Ljl/sat2
pr_number: 202
worker_role: S4
mentor_role: S3
route_id: agent
base_sha: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
purpose:
  - execute agent route task 1
allowed_paths:
  - agent/**
forbidden_paths:
  - paper/**
acceptance:
  - bounded agent result is complete
human_gates: []
""".lstrip()


def progress0(route: str, updated_by: str) -> str:
    return f"""schema: 2
route: {route}
handoff_sequence: 0
parent_sequence: null
event_type: ROUTE_INIT
route_status: ACTIVE
stage: 0
updated_by: {updated_by}
updated_at: 2026-08-12T12:00:00Z
current_task: null
task_id: null
next_task: null
pr_number: null
candidate_sha: null
reviewed_sha: null
control_head_sha: null
task_contract_sha256: null
last_summary: route initialized
"""


def route_config(two_routes: bool = False) -> str:
    second = """
  - route_id: agent
    mentor_role: S3
    worker_role: S4
    pr_number: 202
    progress_file: collaboration/routes/agent/progress.yaml
    progress_ref: relay/agent
    task_root: .sat2/routes/agent/tasks
    bootstrap_task_file: .sat2/routes/agent/tasks/A-01.yml
    signal_mode: progress
""" if two_routes else ""
    return f"""protocol_version: sat2-relay/v2
enabled: true
mode: active
repository: Upp-Ljl/sat2
trusted_actors: [Upp-Ljl]
alert_issue: null
poll_interval_seconds: 60
delivery_lease_seconds: 120
maximum_delivery_attempts: 3
retry_delays_seconds: [1, 2, 3]
process_existing_events_on_first_poll: true
monitors: []
routes:
  - route_id: vision
    mentor_role: S1
    worker_role: S2
    pr_number: 101
    progress_file: collaboration/routes/vision/progress.yaml
    progress_ref: relay/vision
    task_root: .sat2/routes/vision/tasks
    bootstrap_task_file: .sat2/routes/vision/tasks/V-01.yml
    signal_mode: progress
{second}"""


class ParallelGitHub:
    def __init__(self, two_routes: bool = False):
        self.config = route_config(two_routes)
        self.progress = {
            "collaboration/routes/vision/progress.yaml": progress0("vision", "S1"),
            "collaboration/routes/agent/progress.yaml": progress0("agent", "S3"),
        }
        self.tasks = {
            ".sat2/routes/vision/tasks/V-01.yml": TASK1,
            ".sat2/routes/vision/tasks/V-02.yml": TASK2,
            ".sat2/routes/agent/tasks/A-01.yml": AGENT_TASK,
        }
        self.comments = []

    def get_content_text(self, repository, path, ref):
        if path == ".sat2/relay.yml":
            return self.config
        if path in self.progress:
            return self.progress[path]
        if path in self.tasks:
            return self.tasks[path]
        raise AssertionError((path, ref))

    def get_pull_request(self, repository, pr_number):
        head = HEAD1 if pr_number == 101 else HEAD2
        branch = "research/vision" if pr_number == 101 else "research/agent"
        return {"state": "open", "head": {"sha": head, "ref": branch}, "base": {"sha": BASE, "ref": "main"}}

    def list_pull_request_commits(self, repository, pr_number):
        return [{"sha": HEAD1 if pr_number == 101 else HEAD2}]

    def list_pull_request_files(self, repository, pr_number):
        return [{"filename": "vision/impl.py" if pr_number == 101 else "agent/impl.py"}]

    def list_issue_comments(self, repository, pr_number):
        return self.comments

    def create_issue_comment(self, repository, issue_number, body):
        # Progress-active routes must not publish control comments.
        self.comments.append({"id": 999, "body": body, "html_url": "https://example/comment", "user": {"login": "Upp-Ljl"}})
        return self.comments[-1]


def bind(db: RelayDB, roles: dict[str, str]):
    bindings = {
        role: {"url": f"https://chatgpt.com/c/{conversation}", "conversation_key": f"c:{conversation}"}
        for role, conversation in roles.items()
    }
    db.record_heartbeat(
        "installation-123",
        "test",
        json.dumps({"bindings": bindings, "active_roles": list(bindings)}),
    )


def decision(delivery, role: str, conversation: str, name: str, message_hash: str) -> DecisionSubmission:
    return DecisionSubmission(
        installation_id="installation-123",
        role=role,
        conversation_key=f"c:{conversation}",
        delivery_id=delivery.id,
        delivery_token=delivery.delivery_token,
        assistant_message_id=f"msg-{message_hash[:8]}",
        assistant_message_hash=message_hash,
        decision=name,
        summary=f"{role} {name}",
    )


def test_route_aware_target_uses_route_mentor_not_global_mentor():
    monitor = RepoMonitor(pr_number=1, task_id="T", worker_role="S2", mentor_role="S1", task_file="t.yml", allowed_paths=["x/**"])
    event = type("E", (), {"event_type": __import__("sat2_relay.models", fromlist=["EventType"]).EventType.WORKER_CHECKPOINT, "actor_role": "S2"})()
    assert resolve_target(event, monitor) == "S1"


def test_parallel_route_config_rejects_shared_roles_and_shared_control_refs():
    base = {
        "enabled": True,
        "mode": "active",
        "repository": "Upp-Ljl/sat2",
        "trusted_actors": ["Upp-Ljl"],
        "routes": [
            {"route_id": "a", "mentor_role": "S1", "worker_role": "S2", "pr_number": 1, "progress_file": "a/p.yml", "progress_ref": "relay/a", "task_root": ".sat2/a", "signal_mode": "progress"},
            {"route_id": "b", "mentor_role": "S1", "worker_role": "S4", "pr_number": 2, "progress_file": "b/p.yml", "progress_ref": "relay/b", "task_root": ".sat2/b", "signal_mode": "progress"},
        ],
    }
    with pytest.raises(ValueError, match="must not share Session role"):
        RepoRelayConfig.model_validate(base)
    base["routes"][1]["mentor_role"] = "S3"
    base["routes"][1]["progress_ref"] = "relay/a"
    with pytest.raises(ValueError, match="distinct control refs"):
        RepoRelayConfig.model_validate(base)


def test_progress_document_rejects_sequence_gap_shape():
    raw = {
        "schema": 2,
        "route": "vision",
        "handoff_sequence": 3,
        "parent_sequence": 1,
        "event_type": "WORKER_CHECKPOINT",
        "route_status": "ACTIVE",
        "stage": 0,
        "updated_by": "S2",
        "updated_at": "2026-08-12T12:00:00Z",
        "current_task": ".sat2/routes/vision/tasks/V-01.yml",
        "task_id": "V-01",
        "pr_number": 101,
        "candidate_sha": HEAD1,
        "control_head_sha": HEAD1,
        "task_contract_sha256": "d" * 64,
        "last_summary": "checkpoint",
    }
    with pytest.raises(ValueError, match="parent_sequence"):
        ProgressDocument.model_validate(raw)


def test_progress_route_bootstrap_checkpoint_accept_next_task_without_control_comments(local_config):
    db = RelayDB(local_config.database_path)
    gh = ParallelGitHub()
    service = RelayService(local_config, db, gh)

    first = service.poll_once()
    assert first["deliveries"] == 0  # route delivery is created in recover(), not comment polling
    worker = db.lease_next("installation-123", 60, eligible_roles={"S2"})
    assert worker is not None and worker.target_role == "S2"
    assert "PARALLEL ROUTE PROGRESS CONTRACT" in worker.body
    db.complete_delivery(worker.id, True, "DELIVERED", None, 3, [1, 2, 3])
    bind(db, {"S1": "mentor-a", "S2": "worker-a"})

    contract1 = hashlib.sha256(TASK1.encode()).hexdigest()
    gh.progress["collaboration/routes/vision/progress.yaml"] = f"""schema: 2
route: vision
handoff_sequence: 1
parent_sequence: 0
event_type: WORKER_CHECKPOINT
route_status: ACTIVE
stage: 0
updated_by: S2
updated_at: 2026-08-12T12:05:00Z
current_task: .sat2/routes/vision/tasks/V-01.yml
task_id: V-01
next_task: null
pr_number: 101
candidate_sha: {HEAD1}
reviewed_sha: null
control_head_sha: {HEAD1}
task_contract_sha256: {contract1}
last_summary: worker checkpoint
"""
    engine = DecisionEngine(service, db)
    worker_submission = decision(worker, "S2", "worker-a", "WORKER_CHECKPOINT", "1" * 64)
    result = engine.submit(worker_submission)
    assert result["progress"]["sequence"] == 1
    mentor = db.lease_next("installation-123", 60, eligible_roles={"S1"})
    assert mentor is not None and mentor.target_role == "S1"
    db.complete_delivery(mentor.id, True, "DELIVERED", None, 3, [1, 2, 3])

    gh.progress["collaboration/routes/vision/progress.yaml"] = f"""schema: 2
route: vision
handoff_sequence: 2
parent_sequence: 1
event_type: MENTOR_ACCEPTED
route_status: ACTIVE
stage: 1
updated_by: S1
updated_at: 2026-08-12T12:10:00Z
current_task: .sat2/routes/vision/tasks/V-01.yml
task_id: V-01
next_task: .sat2/routes/vision/tasks/V-02.yml
pr_number: 101
candidate_sha: null
reviewed_sha: {HEAD1}
control_head_sha: {HEAD1}
task_contract_sha256: {contract1}
last_summary: accepted and advance
"""
    mentor_submission = decision(mentor, "S1", "mentor-a", "MENTOR_ACCEPTED", "2" * 64)
    accepted = engine.submit(mentor_submission)
    assert accepted["progress"]["sequence"] == 2
    assert db.task_state("V-01")["state"] == "COMPLETE"
    next_worker = db.lease_next("installation-123", 60, eligible_roles={"S2"})
    assert next_worker is not None and "V-02" in next_worker.body
    assert db.get_meta("route:vision:current_task_id") == "V-02"
    assert db.get_meta("route:vision:processed_sequence") == "2"
    assert not gh.comments

    duplicate = engine.submit(mentor_submission)
    assert duplicate["duplicate"] is True
    assert db.lease_next("installation-123", 60, eligible_roles={"S2"}) is None


def test_one_route_sequence_failure_does_not_block_other_route_bootstrap(local_config):
    db = RelayDB(local_config.database_path)
    gh = ParallelGitHub(two_routes=True)
    gh.progress["collaboration/routes/vision/progress.yaml"] = progress0("vision", "S1").replace(
        "handoff_sequence: 0\nparent_sequence: null\nevent_type: ROUTE_INIT",
        "handoff_sequence: 4\nparent_sequence: 3\nevent_type: WORKER_CHECKPOINT",
    ).replace("current_task: null", "current_task: .sat2/routes/vision/tasks/V-01.yml").replace(
        "task_id: null", "task_id: V-01"
    ).replace("pr_number: null", "pr_number: 101").replace(
        "candidate_sha: null", f"candidate_sha: {HEAD1}"
    ).replace("control_head_sha: null", f"control_head_sha: {HEAD1}").replace(
        "task_contract_sha256: null", f"task_contract_sha256: {'d' * 64}"
    ).replace("updated_by: S1", "updated_by: S2")
    service = RelayService(local_config, db, gh)
    service.poll_once()
    assert db.get_meta("route:vision:current_task_id") is None
    assert db.get_meta("route:agent:current_task_id") == "A-01"
    agent_delivery = db.lease_next("installation-123", 60, eligible_roles={"S4"})
    assert agent_delivery is not None and agent_delivery.target_role == "S4"


def test_wrong_route_mentor_cannot_submit_worker_review(local_config):
    db = RelayDB(local_config.database_path)
    gh = ParallelGitHub()
    service = RelayService(local_config, db, gh)
    service.poll_once()
    worker = db.lease_next("installation-123", 60, eligible_roles={"S2"})
    assert worker
    db.complete_delivery(worker.id, True, "DELIVERED", None, 3, [1, 2, 3])
    bind(db, {"S2": "worker-a", "S3": "mentor-b"})
    bad = decision(worker, "S3", "mentor-b", "MENTOR_ACCEPTED", "3" * 64)
    with pytest.raises(DecisionError, match="delivery target"):
        DecisionEngine(service, db).submit(bad)
