from __future__ import annotations

from sat2_relay.db import RelayDB
from sat2_relay.service import RelayService


REPO_CONFIG = """
protocol_version: sat2-relay/v1
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
monitors:
  - pr_number: 32
    task_id: WP-B3
    worker_role: S2
    required_apps: [GitHub]
    strict_apps: true
    task_file: .sat2/tasks/WP-B3.yml
    allowed_paths: [mvp/single_sat_sim_v2/m1/**, tests/single_sat_sim_v2/m1/**, doc/WP-B3.md, .sat2/tasks/WP-B3.yml]
    forbidden_paths: [paper/**, outputs/**, .github/workflows/**]
    dependencies: []
"""

AUTH_BODY = """
Mentor authorization.
<!-- SAT2_RELAY_EVENT_V1 -->
```yaml
protocol: sat2-relay/v1
event_id: WP-B3-auth-1
event_type: SAT2_TASK_AUTHORIZED
repository: Upp-Ljl/sat2
task_id: WP-B3
actor_role: mentor
pr_number: 32
base_sha: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
control_head_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
attempt: 1
timestamp: 2026-07-26T09:00:00+09:00
summary: authorized
```
"""

EVENT_BODY = """
Worker report.
<!-- SAT2_RELAY_EVENT_V1 -->
```yaml
protocol: sat2-relay/v1
event_id: WP-B3-checkpoint-1
event_type: SAT2_WORKER_CHECKPOINT
repository: Upp-Ljl/sat2
task_id: WP-B3
actor_role: S2
pr_number: 32
candidate_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
control_head_sha: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
attempt: 1
timestamp: 2026-07-26T10:00:00+09:00
summary: implementation candidate ready
```
"""


class FakeGitHub:
    def __init__(self):
        self.comments = [
            {"id": 99, "body": AUTH_BODY, "created_at": "2026-07-26T00:00:00Z", "updated_at": "2026-07-26T00:00:00Z", "html_url": "https://github.com/Upp-Ljl/sat2/pull/32#issuecomment-99", "user": {"login": "Upp-Ljl"}},
            {"id": 100, "body": EVENT_BODY, "created_at": "2026-07-26T01:00:00Z", "updated_at": "2026-07-26T01:00:00Z", "html_url": "https://github.com/Upp-Ljl/sat2/pull/32#issuecomment-100", "user": {"login": "Upp-Ljl"}}
        ]
        self.alerts = []
    def get_content_text(self, repository, path, ref):
        if path == ".sat2/relay.yml": return REPO_CONFIG
        if path == ".sat2/tasks/WP-B3.yml": return "task_id: WP-B3\n"
        raise AssertionError(path)
    def get_pull_request(self, repository, pr_number): return {"state": "open", "head": {"sha": "a" * 40}, "base": {"sha": "b" * 40}}
    def list_pull_request_commits(self, repository, pr_number): return [{"sha": "a" * 40}]
    def list_pull_request_files(self, repository, pr_number): return [{"filename": "mvp/single_sat_sim_v2/m1/compiler.py"}]
    def list_issue_comments(self, repository, pr_number): return self.comments
    def create_issue_comment(self, repository, issue_number, body): self.alerts.append(body); return {"html_url": "https://example/alert"}


def test_poll_route_and_dedup(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    first = service.poll_once()
    assert first["events"] == 2
    assert first["deliveries"] == 2
    second = service.poll_once()
    assert second["events"] == 0
    first_delivery = db.lease_next("installation-123", 60)
    assert first_delivery and first_delivery.target_role == "S2"
    db.complete_delivery(first_delivery.id, True, "DELIVERED", None, 3, [1,2,3])
    delivery = db.lease_next("installation-123", 60)
    assert delivery and delivery.target_role == "mentor"
    assert "implementation candidate ready" in delivery.body


def test_untrusted_actor_is_blocked(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    for row in gh.comments: row["user"]["login"] = "attacker"
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    result = service.poll_once()
    assert result["invalid"] == 2
    assert not db.status_snapshot()["deliveries"]


def test_first_poll_baselines_existing_comments(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    gh.get_content_text = lambda repository, path, ref: REPO_CONFIG.replace(
        "process_existing_events_on_first_poll: true", "process_existing_events_on_first_poll: false"
    )
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    result = service.poll_once()
    assert result["baselined"] == 2
    assert result["events"] == 0
    assert not db.status_snapshot()["deliveries"]


def test_control_head_mismatch_blocks_event(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    gh.comments = [dict(gh.comments[0], body=AUTH_BODY.replace("a" * 40, "c" * 40))]
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    result = service.poll_once()
    assert result["invalid"] == 1
    assert not db.status_snapshot()["deliveries"]


def test_out_of_scope_file_blocks_checkpoint(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    gh.list_pull_request_files = lambda repository, pr_number: [{"filename": "paper/main.tex"}]
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    result = service.poll_once()
    # Authorization is valid, checkpoint is blocked by scope.
    assert result["events"] == 1
    assert result["invalid"] == 1
    deliveries = db.status_snapshot()["deliveries"]
    assert len(deliveries) == 1 and deliveries[0]["target_role"] == "S2"


def test_dependency_gate_blocks_authorization(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    gh.get_content_text = lambda repository, path, ref: (
        REPO_CONFIG.replace("dependencies: []", "dependencies: [WP-B2]")
        if path == ".sat2/relay.yml"
        else "task_id: WP-B3\n"
    )
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    result = service.poll_once()
    assert result["invalid"] >= 1
    assert not db.status_snapshot()["deliveries"]


def test_scope_conflict_blocks_second_authorization(local_config):
    db = RelayDB(local_config.database_path)
    gh = FakeGitHub()
    config = REPO_CONFIG.replace(
        "monitors:\n  - pr_number: 32",
        "monitors:\n  - pr_number: 33\n    task_id: WP-OTHER\n    worker_role: S3\n    required_apps: [GitHub]\n    strict_apps: true\n    task_file: .sat2/tasks/WP-OTHER.yml\n    allowed_paths: [mvp/single_sat_sim_v2/m1/**]\n    forbidden_paths: [paper/**, outputs/**, .github/workflows/**]\n    dependencies: []\n  - pr_number: 32",
    )
    gh.get_content_text = lambda repository, path, ref: (
        config if path == ".sat2/relay.yml" else f"task_id: {path.split('/')[-1].replace('.yml','')}\n"
    )
    original_pr = gh.get_pull_request
    gh.get_pull_request = lambda repository, pr_number: original_pr(repository, pr_number)
    original_comments = gh.list_issue_comments
    gh.list_issue_comments = lambda repository, pr_number: (
        [dict(gh.comments[0], body=AUTH_BODY.replace("WP-B3", "WP-OTHER").replace("pr_number: 32", "pr_number: 33"))]
        if pr_number == 33 else original_comments(repository, pr_number)
    )
    service = RelayService(local_config, db, gh)
    service.refresh_config()
    # Seed the other task as active, then ensure WP-B3 authorization fails closed.
    db.update_task_state("WP-OTHER", "WORKING", "seed", "S3", 33, "a" * 40)
    result = service.poll_once()
    assert result["invalid"] >= 1
