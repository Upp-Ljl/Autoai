from __future__ import annotations

import fnmatch
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import yaml

from .config import LocalConfig, parse_repository_config
from .db import RelayDB
from .github import GitHubClient, GitHubError
from .models import EventType, RelayEvent, RelayMode, RepoMonitor, RepoRelayConfig
from .protocol import (
    build_execution_capsule,
    delivery_marker,
    extract_event_documents,
    resolve_target,
    transition_name,
    validate_actor_semantics,
    validate_event_document,
    validate_pr_binding,
    validate_transition,
)

LOG = logging.getLogger(__name__)


class TaskSpecUnavailable(RuntimeError):
    pass


class TaskSpecInvalid(ValueError):
    pass


@dataclass(frozen=True)
class TaskSpecResolution:
    path: str
    ref: str | None
    text: str
    sha256: str
    document: dict[str, Any]


class RelayService:
    def __init__(self, local: LocalConfig, db: RelayDB, github: GitHubClient, alert_github: GitHubClient | None = None):
        self.local = local
        self.db = db
        self.github = github
        self.alert_github = alert_github or github
        self.repo_config: RepoRelayConfig | None = None

    def replace_github_clients(self, github: GitHubClient, alert_github: GitHubClient | None = None) -> None:
        old = self.github
        old_alert = self.alert_github
        self.github = github
        self.alert_github = alert_github or github
        if old is not github:
            old.close()
        if old_alert not in {old, self.alert_github}:
            old_alert.close()
        self.repo_config = None
        self.db.set_meta("credentials_reloaded_at", datetime.now(UTC).isoformat())

    def refresh_config(self) -> RepoRelayConfig:
        text = self.github.get_content_text(
            self.local.github_repository,
            self.local.repository_config_path,
            self.local.repository_config_ref,
        )
        config = parse_repository_config(text)
        if config.repository != self.local.github_repository:
            raise ValueError("repository relay config points to another repository")
        self.repo_config = config
        self.db.set_meta("last_config_refresh", datetime.now(UTC).isoformat())
        self.db.set_meta("config_ref", self.local.repository_config_ref)
        self.db.set_meta("config_sha256", hashlib.sha256(text.encode()).hexdigest())
        return config

    def effective_mode(self) -> RelayMode:
        if self.local.local_mode_override:
            return self.local.local_mode_override
        if not self.repo_config:
            return RelayMode.PAUSED
        if not self.repo_config.enabled:
            return RelayMode.SHADOW
        return self.repo_config.mode

    def _alert(
        self,
        severity: str,
        code: str,
        detail: str,
        task_id: str | None = None,
        pr_number: int | None = None,
        cooldown_seconds: int = 0,
    ) -> None:
        if cooldown_seconds and self.db.has_recent_alert(code, cooldown_seconds, task_id, pr_number):
            return
        url = None
        config = self.repo_config
        if self.local.allow_github_writes and config and config.alert_issue and self.local.github_alert_secret.value:
            mention = f"@{config.alert_mention} " if config.alert_mention else ""
            body = (
                f"{mention}SAT2 Relay alert\n\n"
                f"- Severity: `{severity}`\n- Code: `{code}`\n- Task: `{task_id or 'n/a'}`\n"
                f"- PR: `{pr_number or 'n/a'}`\n- Time: `{datetime.now(UTC).isoformat()}`\n\n{detail}"
            )
            try:
                result = self.alert_github.create_issue_comment(config.repository, config.alert_issue, body)
                url = result.get("html_url")
            except Exception as exc:  # noqa: BLE001
                detail = f"{detail}\nGitHub alert write also failed: {exc}"
        self.db.add_alert(severity, code, detail, task_id, pr_number, url)

    def _baseline_monitor(self, monitor: RepoMonitor, comments: list[dict[str, Any]]) -> bool:
        assert self.repo_config is not None
        key = f"monitor_initialized:{self.repo_config.repository}:{monitor.pr_number}:{monitor.task_id}"
        if self.db.get_meta(key):
            return False
        if self.repo_config.process_existing_events_on_first_poll:
            self.db.set_meta(key, datetime.now(UTC).isoformat())
            return False
        # An explicit start boundary is authoritative. Baseline only comments at
        # or before that boundary and process newer comments in the same first
        # poll. This prevents a freshly installed daemon from swallowing the
        # first authorization that was posted after the reviewed boundary.
        if monitor.start_after_comment_id:
            for comment in comments:
                if int(comment["id"]) <= monitor.start_after_comment_id:
                    self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, "baseline")
            self.db.set_meta(key, datetime.now(UTC).isoformat())
            return False
        for comment in comments:
            self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, "baseline")
        self.db.set_meta(key, datetime.now(UTC).isoformat())
        return True

    def _check_extension_health(self) -> None:
        config = self.repo_config
        if not config or self.effective_mode() not in {RelayMode.DRY_RUN, RelayMode.ACTIVE}:
            return
        if self.db.pending_delivery_count() == 0:
            return
        heartbeat = self.db.latest_heartbeat()
        if heartbeat:
            try:
                age = (datetime.now(UTC) - datetime.fromisoformat(heartbeat["last_seen"])).total_seconds()
            except ValueError:
                age = config.extension_stale_seconds + 1
        else:
            age = config.extension_stale_seconds + 1
        if age > config.extension_stale_seconds:
            self._alert(
                "warning",
                "BROWSER_RELAY_OFFLINE",
                f"Pending deliveries exist but the browser extension heartbeat is {int(age)} seconds old.",
                cooldown_seconds=config.extension_stale_seconds,
            )

    @staticmethod
    def _path_matches(path: str, patterns: list[str]) -> bool:
        return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)

    @staticmethod
    def _path_prefix(pattern: str) -> str:
        wildcard = min([i for i in (pattern.find("*"), pattern.find("?"), pattern.find("[")) if i >= 0] or [len(pattern)])
        return pattern[:wildcard].rstrip("/")

    def _validate_dependencies(self, monitor: RepoMonitor) -> None:
        for dependency in monitor.dependencies:
            state = self.db.task_state(dependency)
            if not state or state["state"] not in {"ACCEPTED", "COMPLETE"}:
                raise ValueError(f"dependency {dependency} is not COMPLETE in Relay state")

    def _validate_scope_conflicts(self, monitor: RepoMonitor) -> None:
        if not self.repo_config:
            return
        current_prefixes = [self._path_prefix(p) for p in monitor.allowed_paths if self._path_prefix(p)]
        for other in self.repo_config.monitors:
            if other.task_id == monitor.task_id or not other.enabled:
                continue
            state = self.db.task_state(other.task_id)
            if not state or state["state"] not in {"DISPATCHED", "WORKING", "MENTOR_REVIEW", "HUMAN_GATE"}:
                continue
            other_prefixes = [self._path_prefix(p) for p in other.allowed_paths if self._path_prefix(p)]
            for left in current_prefixes:
                for right in other_prefixes:
                    if left == right or left.startswith(right + "/") or right.startswith(left + "/"):
                        raise ValueError(f"write-scope conflict with active task {other.task_id}: {left} vs {right}")

    def resolve_task_ref(self, monitor: RepoMonitor, pr: dict[str, Any]) -> str | None:
        value = monitor.task_ref
        if value == "@config":
            return self.local.repository_config_ref
        if value == "@default":
            return None
        if value == "@pr-head":
            return str((pr.get("head") or {}).get("sha") or "")
        if value == "@pr-base":
            return str((pr.get("base") or {}).get("sha") or "")
        return value

    def load_task_spec(self, monitor: RepoMonitor, pr: dict[str, Any]) -> TaskSpecResolution:
        assert self.repo_config is not None
        if not monitor.task_file:
            raise TaskSpecInvalid("enabled monitor does not declare task_file")
        ref = self.resolve_task_ref(monitor, pr)
        try:
            text = self.github.get_content_text(self.repo_config.repository, monitor.task_file, ref)
        except GitHubError as exc:
            context = {
                "stage": "task_spec_fetch",
                "repository": self.repo_config.repository,
                "path": monitor.task_file,
                "ref": ref or "<default-branch>",
                "token_source": getattr(self.github, "token_source", "unknown"),
                "token_fingerprint": getattr(self.github, "token_fingerprint", None),
                "github_error": str(exc),
                "recovery": "Fix task_ref/path or reload credentials; the same comment will be replayed automatically.",
            }
            raise TaskSpecUnavailable(json.dumps(context, ensure_ascii=False, sort_keys=True)) from exc
        digest = hashlib.sha256(text.encode()).hexdigest()
        if monitor.task_sha256 and digest != monitor.task_sha256:
            raise TaskSpecInvalid(f"task specification digest mismatch: expected {monitor.task_sha256}, got {digest}")
        try:
            document = yaml.safe_load(text)
        except Exception as exc:  # noqa: BLE001
            raise TaskSpecInvalid(f"task specification YAML cannot be parsed: {exc}") from exc
        if not isinstance(document, dict):
            raise TaskSpecInvalid("task specification must be a mapping")
        if str(document.get("task_id")) != monitor.task_id:
            raise TaskSpecInvalid(f"task specification binds task_id {document.get('task_id')!r}, expected {monitor.task_id}")
        if document.get("repository") and str(document["repository"]) != self.repo_config.repository:
            raise TaskSpecInvalid("task specification repository does not match Relay repository")
        if document.get("pr_number") and int(document["pr_number"]) != monitor.pr_number:
            raise TaskSpecInvalid("task specification pr_number does not match monitor")
        if document.get("worker_role") and str(document["worker_role"]).upper() != monitor.worker_role:
            raise TaskSpecInvalid("task specification worker_role does not match monitor")
        task_allowed = set(map(str, document.get("allowed_paths") or []))
        task_forbidden = set(map(str, document.get("forbidden_paths") or []))
        if task_allowed and task_allowed != set(monitor.allowed_paths):
            raise TaskSpecInvalid("task specification allowed_paths differ from relay.yml monitor; use one reviewed exact list")
        if task_forbidden and task_forbidden != set(monitor.forbidden_paths):
            raise TaskSpecInvalid("task specification forbidden_paths differ from relay.yml monitor; use one reviewed exact list")
        self.db.set_meta(f"task_spec:{monitor.task_id}:path", monitor.task_file)
        self.db.set_meta(f"task_spec:{monitor.task_id}:ref", ref or "<default-branch>")
        self.db.set_meta(f"task_spec:{monitor.task_id}:sha256", digest)
        return TaskSpecResolution(monitor.task_file, ref, text, digest, document)

    def _validate_monitor_contract(
        self,
        monitor: RepoMonitor,
        pr: dict[str, Any],
        changed_files: list[str],
        event: RelayEvent,
    ) -> TaskSpecResolution | None:
        task_spec = None
        if event.event_type is EventType.TASK_AUTHORIZED:
            task_spec = self.load_task_spec(monitor, pr)
            if event.task_spec_sha256 and event.task_spec_sha256 != task_spec.sha256:
                raise TaskSpecInvalid(
                    f"event task_spec_sha256 mismatch: event {event.task_spec_sha256}, resolved {task_spec.sha256}"
                )
            self._validate_dependencies(monitor)
            self._validate_scope_conflicts(monitor)
        if event.event_type in {EventType.WORKER_CHECKPOINT, EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED}:
            violations = [
                path
                for path in changed_files
                if not self._path_matches(path, monitor.allowed_paths) or self._path_matches(path, monitor.forbidden_paths)
            ]
            if violations:
                raise ValueError(f"PR contains paths outside Relay scope: {violations[:20]}")
        return task_spec

    def _process_event(
        self,
        monitor: RepoMonitor,
        pr: dict[str, Any],
        commit_shas: set[str],
        changed_files: list[str],
        comment: dict[str, Any],
        raw: dict[str, Any],
    ) -> tuple[bool, bool]:
        assert self.repo_config is not None
        event = validate_event_document(raw)
        if event.repository != self.repo_config.repository or event.pr_number != monitor.pr_number or event.task_id != monitor.task_id:
            raise ValueError("event repository/PR/task does not match monitor")
        event.source_comment_id = int(comment["id"])
        event.source_comment_url = str(comment.get("html_url") or "")
        event.source_actor = str((comment.get("user") or {}).get("login") or "")
        validate_actor_semantics(event, monitor)
        validate_pr_binding(event, pr, commit_shas)
        self._validate_monitor_contract(monitor, pr, changed_files, event)
        previous = self.db.task_state(event.task_id)
        validate_transition(
            event,
            previous["state"] if previous else None,
            previous["last_event_id"] if previous else None,
        )
        target = resolve_target(event, monitor)
        mode = self.effective_mode()
        marker = delivery_marker(event, target) if target else None
        delivery_token = secrets.token_urlsafe(24) if target else None
        capsule = build_execution_capsule(event, target, marker, monitor, delivery_token) if target else None
        new_state = transition_name(event)
        state_sha = event.candidate_sha or event.reviewed_sha or event.authorized_sha or event.base_sha
        inserted, delivery_id = self.db.accept_event(
            event,
            event.model_dump_json(),
            new_state=new_state,
            worker_role=monitor.worker_role,
            state_sha=state_sha,
            target_role=target if mode is not RelayMode.SHADOW else None,
            body=capsule if mode is not RelayMode.SHADOW else None,
            delivery_token=delivery_token,
            required_apps=monitor.required_apps,
            strict_apps=monitor.strict_apps,
            awaiting_approval=mode is RelayMode.DRY_RUN,
        )
        if not inserted:
            return False, False
        if mode is RelayMode.SHADOW and target:
            self.db.add_alert("info", "SHADOW_DISPATCH", f"Would dispatch {event.event_id} to {target}", event.task_id, event.pr_number)
        if event.event_type in {EventType.HUMAN_GATE, EventType.TASK_BLOCKED, EventType.RELAY_ALERT}:
            self._alert("warning", event.event_type.value, event.summary or "Task requires intervention", event.task_id, event.pr_number)
        self.db.resolve_alerts(code="TASK_SPEC_UNAVAILABLE", task_id=event.task_id, pr_number=event.pr_number)
        self.db.resolve_alerts(code="PROTOCOL_OR_STATE_INVALID", task_id=event.task_id, pr_number=event.pr_number)
        return True, delivery_id is not None

    def poll_once(self) -> dict[str, int]:
        self.refresh_config()
        assert self.repo_config is not None
        from .decisions import DecisionEngine
        outbox_counts = DecisionEngine(self, self.db).recover()
        counts = {
            "comments": 0,
            "events": 0,
            "deliveries": 0,
            "invalid": 0,
            "retryable": 0,
            "ignored": 0,
            "baselined": 0,
            "outbox_published": outbox_counts.get("published", 0),
            "outbox_retry": outbox_counts.get("retry", 0),
            "outbox_blocked": outbox_counts.get("blocked", 0),
        }
        self.db.recover_expired_leases()
        if self.effective_mode() is RelayMode.PAUSED:
            self.db.set_meta("last_poll", datetime.now(UTC).isoformat())
            return counts
        for monitor in self.repo_config.monitors:
            if not monitor.enabled:
                continue
            try:
                pr = self.github.get_pull_request(self.repo_config.repository, monitor.pr_number)
                if pr.get("state") != "open":
                    self._alert("error", "PR_NOT_OPEN", f"Monitored PR #{monitor.pr_number} is {pr.get('state')}", monitor.task_id, monitor.pr_number, 300)
                    continue
                comments = self.github.list_issue_comments(self.repo_config.repository, monitor.pr_number)
                commit_shas: set[str] | None = None
                changed_files: list[str] | None = None
            except GitHubError as exc:
                code = "GITHUB_AUTH_FAILED" if exc.status_code in {401, 403, 404} else "GITHUB_POLL_FAILED"
                detail = json.dumps(
                    {
                        "stage": "monitor_poll",
                        "error": str(exc),
                        "token_source": getattr(self.github, "token_source", "unknown"),
                        "token_fingerprint": getattr(self.github, "token_fingerprint", None),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                self._alert("error", code, detail, monitor.task_id, monitor.pr_number, self.repo_config.error_retry_seconds)
                continue
            if self._baseline_monitor(monitor, comments):
                counts["baselined"] += len(comments)
                continue
            for comment in comments:
                comment_id = int(comment["id"])
                if monitor.start_after_comment_id and comment_id <= monitor.start_after_comment_id:
                    if not self.db.comment_status(self.repo_config.repository, monitor.pr_number, comment_id):
                        self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, "before_start_boundary")
                    counts["ignored"] += 1
                    continue
                counts["comments"] += 1
                existing = self.db.comment_status(self.repo_config.repository, monitor.pr_number, comment_id)
                body = str(comment.get("body") or "")
                body_hash = hashlib.sha256(body.encode()).hexdigest()
                updated = str(comment.get("updated_at") or comment.get("created_at"))
                if self.db.comment_is_final(existing, updated, body_hash):
                    counts["ignored"] += 1
                    continue
                if existing and existing["body_hash"] != body_hash and existing["outcome"] != "retryable_error":
                    self._alert("error", "PROCESSED_COMMENT_EDITED", f"Previously processed control comment {comment_id} changed.", monitor.task_id, monitor.pr_number)
                    self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, "edited_after_processing")
                    counts["invalid"] += 1
                    continue
                try:
                    docs = extract_event_documents(body)
                except Exception as exc:  # noqa: BLE001
                    self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, "invalid_block", str(exc))
                    self._alert("error", "PROTOCOL_INVALID", f"Comment {comment_id}: {exc}", monitor.task_id, monitor.pr_number)
                    counts["invalid"] += 1
                    continue
                if not docs:
                    self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, "no_event")
                    continue
                actor = str((comment.get("user") or {}).get("login") or "")
                if actor not in self.repo_config.trusted_actors:
                    self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, "untrusted_actor")
                    self._alert("error", "UNTRUSTED_ACTOR", f"Control event comment {comment_id} was posted by {actor}", monitor.task_id, monitor.pr_number)
                    counts["invalid"] += 1
                    continue
                outcome = "processed"
                error_detail = None
                if commit_shas is None or changed_files is None:
                    try:
                        commits = self.github.list_pull_request_commits(self.repo_config.repository, monitor.pr_number)
                        commit_shas = {str(row.get("sha") or "") for row in commits}
                        changed_files = [str(row.get("filename") or "") for row in self.github.list_pull_request_files(self.repo_config.repository, monitor.pr_number)]
                    except GitHubError as exc:
                        outcome = "retryable_error"
                        error_detail = str(exc)
                        counts["retryable"] += 1
                        self._alert("error", "GITHUB_EVENT_VALIDATION_FAILED", f"Comment {comment_id}: {exc}", monitor.task_id, monitor.pr_number, 60)
                        self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, outcome, error_detail)
                        continue
                for raw in docs:
                    try:
                        inserted, delivered = self._process_event(monitor, pr, commit_shas, changed_files, comment, raw)
                        counts["events"] += int(inserted)
                        counts["deliveries"] += int(delivered)
                    except TaskSpecUnavailable as exc:
                        outcome = "retryable_error"
                        error_detail = str(exc)
                        counts["retryable"] += 1
                        self._alert("error", "TASK_SPEC_UNAVAILABLE", f"Comment {comment_id}: {exc}", monitor.task_id, monitor.pr_number, 60)
                        break
                    except GitHubError as exc:
                        outcome = "retryable_error"
                        error_detail = str(exc)
                        counts["retryable"] += 1
                        self._alert("error", "GITHUB_EVENT_VALIDATION_FAILED", f"Comment {comment_id}: {exc}", monitor.task_id, monitor.pr_number, 60)
                        break
                    except Exception as exc:  # noqa: BLE001
                        outcome = "invalid_event"
                        error_detail = str(exc)
                        counts["invalid"] += 1
                        self._alert("error", "PROTOCOL_OR_STATE_INVALID", f"Comment {comment_id}: {exc}", monitor.task_id, monitor.pr_number)
                        break
                self.db.record_comment(self.repo_config.repository, monitor.pr_number, comment, outcome, error_detail)
        self._check_extension_health()
        self.db.set_meta("last_poll", datetime.now(UTC).isoformat())
        self.db.set_meta("last_poll_counts", json.dumps(counts, sort_keys=True))
        self.db.set_meta("last_poll_error", "")
        return counts

    def doctor(self, deep: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": True,
            "version": "2.2.0",
            "config": str(self.local.path),
            "database": str(self.local.database_path),
            "loopback_only": self.local.host in {"127.0.0.1", "localhost", "::1"},
            "github_token": {
                "present": bool(getattr(self.github, "token", None)),
                "source": getattr(self.github, "token_source", "unknown"),
                "fingerprint": getattr(self.github, "token_fingerprint", None),
            },
            "github_rate_limit": getattr(self.github, "rate_limit", {}),
            "checks": [],
        }

        def check(name: str, fn) -> Any:
            try:
                value = fn()
                result["checks"].append({"name": name, "ok": True, "detail": value})
                return value
            except Exception as exc:  # noqa: BLE001
                result["ok"] = False
                result["checks"].append({"name": name, "ok": False, "detail": str(exc)})
                return None

        check("database_write", lambda: (self.db.set_meta("doctor_write_test", datetime.now(UTC).isoformat()), "ok")[1])
        check("github_control_writes", lambda: "enabled" if self.local.allow_github_writes else (_ for _ in ()).throw(ValueError("github.allow_writes is false; automatic Session decisions cannot publish")))
        check("repository_metadata", lambda: {k: self.github.get_repository(self.local.github_repository).get(k) for k in ("full_name", "private", "default_branch")})
        config = check("repository_config", self.refresh_config)
        heartbeat = self.db.latest_heartbeat()
        if heartbeat:
            def heartbeat_detail():
                payload = json.loads(heartbeat["payload_json"])
                bindings = payload.get("bindings") or {}
                keys = {}
                duplicates = []
                for role, binding in bindings.items():
                    if isinstance(binding, dict):
                        key = binding.get("conversation_key")
                    else:
                        key = None
                    if key and key in keys:
                        duplicates.append({"conversation_key": key, "roles": [keys[key], role]})
                    elif key:
                        keys[key] = role
                age = (datetime.now(UTC) - datetime.fromisoformat(heartbeat["last_seen"])).total_seconds()
                extension_version = str(heartbeat["extension_version"] or payload.get("extension_version") or "")
                if config and age > config.extension_stale_seconds:
                    raise ValueError(f"extension heartbeat stale by {int(age)} seconds")
                if extension_version and not extension_version.startswith("2.2"):
                    raise ValueError(f"extension version {extension_version} is incompatible with daemon 2.2.0")
                if duplicates:
                    raise ValueError(f"duplicate Session bindings: {duplicates}")
                active_roles = payload.get("active_roles") or []
                return {
                    "last_seen": heartbeat["last_seen"],
                    "age_seconds": int(age),
                    "extension_version": extension_version,
                    "active_roles": active_roles,
                    "duplicates": duplicates,
                }

            check("extension_heartbeat", heartbeat_detail)
        elif deep:
            check("extension_heartbeat", lambda: (_ for _ in ()).throw(ValueError("no extension heartbeat recorded")))

        if config:
            def role_routing_detail():
                endpoints = self.db.fresh_role_endpoints(config.extension_stale_seconds)
                required_roles = {"mentor"}
                required_roles.update(monitor.worker_role for monitor in config.monitors if monitor.enabled)
                by_role: dict[str, list[dict[str, Any]]] = {}
                conversation_owners: dict[str, str] = {}
                duplicate_conversations = []
                for endpoint in endpoints:
                    role = str(endpoint["role"])
                    by_role.setdefault(role, []).append(endpoint)
                    key = endpoint.get("conversation_key")
                    if key and key in conversation_owners and conversation_owners[key] != role:
                        duplicate_conversations.append({
                            "conversation_key": key,
                            "roles": sorted({conversation_owners[key], role}),
                        })
                    elif key:
                        conversation_owners[key] = role
                missing = sorted(role for role in required_roles if not by_role.get(role))
                ambiguous = {
                    role: sorted({str(row.get("conversation_key") or row.get("url")) for row in rows})
                    for role, rows in by_role.items()
                    if len({str(row.get("conversation_key") or row.get("url")) for row in rows}) > 1
                }
                if missing:
                    raise ValueError(f"required role endpoints missing or stale: {missing}")
                if duplicate_conversations:
                    raise ValueError(f"duplicate Session bindings across roles/installations: {duplicate_conversations}")
                if ambiguous:
                    raise ValueError(f"roles have multiple fresh endpoints: {ambiguous}")
                inactive = sorted(
                    role for role in required_roles
                    if not any(bool(row.get("active")) for row in by_role.get(role, []))
                )
                return {
                    "required_roles": sorted(required_roles),
                    "routable_roles": sorted(by_role),
                    "inactive_required_roles": inactive,
                    "endpoints": [
                        {
                            "role": row["role"],
                            "installation_id": row["installation_id"],
                            "conversation_key": row.get("conversation_key"),
                            "url": row["url"],
                            "active": bool(row["active"]),
                            "last_seen": row["last_seen"],
                        }
                        for row in endpoints
                    ],
                }

            check("role_routing", role_routing_detail)

        if deep and config:
            for monitor in config.monitors:
                if not monitor.enabled:
                    continue
                pr = check(f"monitor:{monitor.task_id}:pr", lambda m=monitor: self.github.get_pull_request(config.repository, m.pr_number))
                if pr:
                    def task_detail(m=monitor, p=pr):
                        resolved = self.load_task_spec(m, p)
                        return {"path": resolved.path, "ref": resolved.ref, "sha256": resolved.sha256}

                    check(f"monitor:{monitor.task_id}:task_spec", task_detail)
                    check(
                        f"monitor:{monitor.task_id}:comments",
                        lambda m=monitor: {"count": len(self.github.list_issue_comments(config.repository, m.pr_number))},
                    )
        return result
