from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import yaml

from .db import RelayDB
from .github import GitHubError
from .models import (
    DecisionName,
    DecisionSubmission,
    EventType,
    OutboxStatus,
    RelayEvent,
    RepoMonitor,
)
from .protocol import resolve_target, validate_actor_semantics, validate_pr_binding, validate_transition


class DecisionError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


DECISION_EVENT = {
    DecisionName.WORKER_ACK: EventType.WORKER_ACK,
    DecisionName.WORKER_CHECKPOINT: EventType.WORKER_CHECKPOINT,
    DecisionName.MENTOR_CHANGES_REQUIRED: EventType.MENTOR_CHANGES_REQUIRED,
    DecisionName.MENTOR_ACCEPTED: EventType.MENTOR_ACCEPTED,
    DecisionName.TASK_BLOCKED: EventType.TASK_BLOCKED,
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _event_id(task_id: str, digest: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in task_id)[:80]
    return f"{safe}.auto.{digest[:32]}"


def _event_marker(digest: str) -> str:
    return f"sat2-relay-auto-{digest[:40]}"


def _event_comment(event: RelayEvent, marker: str) -> str:
    payload = event.model_dump(mode="json", exclude_none=True)
    for key in ("source_comment_id", "source_comment_url", "source_actor"):
        payload.pop(key, None)
    document = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=120).rstrip()
    return (
        f"<!-- SAT2_RELAY_AUTO_EVENT:{marker} -->\n"
        f"<!-- SAT2_RELAY_EVENT_V2 -->\n"
        f"```yaml\n{document}\n```\n\n"
        f"Relay-generated control event. Session-authored control fields are not accepted.\n"
    )


class DecisionEngine:
    def __init__(self, service: Any, db: RelayDB):
        self.service = service
        self.db = db

    def _config(self):
        return self.service.refresh_config()

    @staticmethod
    def _head(pr: dict[str, Any]) -> str:
        return str((pr.get("head") or {}).get("sha") or "")

    @staticmethod
    def _base(pr: dict[str, Any]) -> str:
        return str((pr.get("base") or {}).get("sha") or "")

    def _monitor(self, task_id: str) -> RepoMonitor:
        config = self.service.repo_config or self._config()
        for monitor in config.monitors:
            if monitor.task_id == task_id and monitor.enabled:
                return monitor
        raise DecisionError("TASK_NOT_MONITORED", f"No enabled monitor exists for task {task_id}")

    def _endpoint(self, submission: DecisionSubmission) -> dict[str, Any]:
        config = self.service.repo_config or self._config()
        endpoint = self.db.endpoint_for(submission.installation_id, submission.role, config.extension_stale_seconds)
        if not endpoint:
            raise DecisionError("ENDPOINT_NOT_BOUND", f"No fresh endpoint for {submission.role} on installation {submission.installation_id}")
        if str(endpoint.get("conversation_key") or "") != submission.conversation_key:
            raise DecisionError("CONVERSATION_MISMATCH", "Decision conversation does not match the bound endpoint")
        return endpoint

    @staticmethod
    def _validate_role_decision(role: str, decision: DecisionName) -> None:
        worker_allowed = {DecisionName.WORKER_ACK, DecisionName.WORKER_CHECKPOINT, DecisionName.TASK_BLOCKED}
        mentor_allowed = {DecisionName.MENTOR_CHANGES_REQUIRED, DecisionName.MENTOR_ACCEPTED, DecisionName.TASK_BLOCKED}
        allowed = mentor_allowed if role == "mentor" else worker_allowed
        if decision not in allowed:
            raise DecisionError("ROLE_DECISION_MISMATCH", f"Role {role} cannot submit {decision.value}")

    @staticmethod
    def _validate_state(decision: DecisionName, state: str | None) -> None:
        allowed: dict[DecisionName, set[str | None]] = {
            DecisionName.WORKER_ACK: {"DISPATCHED"},
            DecisionName.WORKER_CHECKPOINT: {"DISPATCHED", "WORKING"},
            DecisionName.MENTOR_CHANGES_REQUIRED: {"MENTOR_REVIEW"},
            DecisionName.MENTOR_ACCEPTED: {"MENTOR_REVIEW"},
            DecisionName.TASK_BLOCKED: {"DISPATCHED", "WORKING", "MENTOR_REVIEW"},
        }
        if state not in allowed[decision]:
            raise DecisionError("ILLEGAL_STATE_TRANSITION", f"State {state!r} does not allow {decision.value}")

    @staticmethod
    def _validate_delivery_origin(role: str, decision: DecisionName, event_type: str) -> None:
        if role == "mentor" and event_type != EventType.WORKER_CHECKPOINT.value:
            raise DecisionError("DELIVERY_CAUSATION_MISMATCH", "Mentor decisions require a Worker checkpoint Capsule")
        if role != "mentor" and event_type not in {
            EventType.TASK_AUTHORIZED.value,
            EventType.MENTOR_CHANGES_REQUIRED.value,
        }:
            raise DecisionError("DELIVERY_CAUSATION_MISMATCH", "Worker decisions require a task-dispatch or changes-required Capsule")
        if decision is DecisionName.WORKER_ACK and event_type not in {
            EventType.TASK_AUTHORIZED.value,
            EventType.MENTOR_CHANGES_REQUIRED.value,
        }:
            raise DecisionError("DELIVERY_CAUSATION_MISMATCH", "Worker ACK is not bound to a Worker task Capsule")

    def _event_from_submission(
        self,
        submission: DecisionSubmission,
        context: dict[str, Any],
        monitor: RepoMonitor,
        pr: dict[str, Any],
        digest: str,
    ) -> RelayEvent:
        task_state = str(context.get("task_state") or "") or None
        self._validate_state(submission.decision, task_state)
        parent_event_id = str(context.get("last_event_id") or "") or None
        if not parent_event_id:
            raise DecisionError("PARENT_EVENT_MISSING", "The local ledger has no current parent event")
        parent = self.db.event_payload(parent_event_id)
        if not parent:
            raise DecisionError("PARENT_EVENT_MISSING", f"Parent event {parent_event_id} is absent from the local ledger")
        head = self._head(pr)
        base = self._base(pr)
        event_type = DECISION_EVENT[submission.decision]
        correlation = str(parent.get("correlation_id") or parent.get("event_id") or parent_event_id)
        fields: dict[str, Any] = {
            "protocol": "sat2-relay/v2",
            "event_id": _event_id(monitor.task_id, digest),
            "event_type": event_type.value,
            "repository": self.service.repo_config.repository,
            "task_id": monitor.task_id,
            "actor_role": submission.role,
            "target_role": resolve_target(RelayEvent.model_construct(event_type=event_type), monitor),
            "pr_number": monitor.pr_number,
            "parent_event_id": parent_event_id,
            "correlation_id": correlation,
            "control_head_sha": head,
            "attempt": self.db.next_event_attempt(monitor.task_id, event_type.value),
            "timestamp": datetime.now(UTC),
            "summary": submission.summary,
        }
        if event_type is EventType.WORKER_CHECKPOINT:
            fields["candidate_sha"] = head
        elif event_type in {EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED}:
            if str(parent.get("event_type")) != EventType.WORKER_CHECKPOINT.value:
                raise DecisionError("PARENT_EVENT_MISMATCH", "Mentor review parent is not a Worker checkpoint")
            reviewed = str(parent.get("candidate_sha") or "")
            if not reviewed:
                raise DecisionError("CHECKPOINT_SHA_MISSING", "The checkpoint event has no candidate_sha")
            if reviewed != head:
                raise DecisionError("STALE_PR_HEAD", f"Checkpoint SHA {reviewed} differs from current PR head {head}")
            fields["reviewed_sha"] = reviewed
        elif event_type in {EventType.WORKER_ACK, EventType.TASK_BLOCKED}:
            fields["control_head_sha"] = head
        event = RelayEvent.model_validate(fields)
        validate_actor_semantics(event, monitor)
        commits = {
            str(row.get("sha") or "")
            for row in self.service.github.list_pull_request_commits(self.service.repo_config.repository, monitor.pr_number)
        }
        validate_pr_binding(event, pr, commits)
        validate_transition(event, task_state, parent_event_id)
        changed = [
            str(row.get("filename") or "")
            for row in self.service.github.list_pull_request_files(self.service.repo_config.repository, monitor.pr_number)
        ]
        self.service._validate_monitor_contract(monitor, pr, changed, event)
        if base != str((pr.get("base") or {}).get("sha") or ""):
            raise DecisionError("STALE_PR_BASE", "PR base changed during event composition")
        return event

    def submit(self, submission: DecisionSubmission) -> dict[str, Any]:
        config = self._config()
        self._validate_role_decision(submission.role, submission.decision)
        self._endpoint(submission)
        context = self.db.delivery_context(submission.delivery_id)
        if not context:
            raise DecisionError("NO_ACTIVE_DELIVERY", f"Delivery {submission.delivery_id} does not exist")
        if context.get("status") != "delivered":
            raise DecisionError("DELIVERY_NOT_CONFIRMED", f"Delivery {submission.delivery_id} is {context.get('status')}")
        if str(context.get("delivery_token") or "") != submission.delivery_token:
            raise DecisionError("DELIVERY_TOKEN_MISMATCH", "Decision token does not match the active Capsule")
        if str(context.get("target_role") or "") != submission.role:
            raise DecisionError("ROLE_DELIVERY_MISMATCH", "Decision role does not match the delivery target")
        duplicate = self.db.decision_exists_for_message(
            submission.delivery_id, submission.assistant_message_hash, submission.decision.value
        )
        if duplicate:
            return {"ok": True, "duplicate": True, "outbox": dict(duplicate)}
        if context.get("decision_consumed_at") and submission.decision is not DecisionName.WORKER_ACK:
            raise DecisionError("DELIVERY_ALREADY_CONSUMED", "The active Capsule already produced a terminal decision")
        self._validate_delivery_origin(submission.role, submission.decision, str(context.get("event_type") or ""))
        if submission.decision is DecisionName.WORKER_ACK and self.db.ack_exists_for_delivery(submission.delivery_id):
            existing = self.db.decision_exists_for_message(
                submission.delivery_id, submission.assistant_message_hash, submission.decision.value
            )
            if existing:
                return {"ok": True, "duplicate": True, "outbox": dict(existing)}
            raise DecisionError("ACK_ALREADY_RECORDED", "This Capsule already produced a Worker ACK")
        # WORKER_ACK is legacy/informational only. A checkpoint never waits for
        # an ACK; the confirmed delivery marker already proves receipt.

        task_id = str(context["task_id"])
        monitor = self._monitor(task_id)
        if submission.role != "mentor" and submission.role != monitor.worker_role:
            raise DecisionError("ROLE_DECISION_MISMATCH", f"Task {task_id} is assigned to {monitor.worker_role}, not {submission.role}")
        pr = self.service.github.get_pull_request(config.repository, monitor.pr_number)
        if pr.get("state") != "open":
            raise DecisionError("PR_NOT_OPEN", f"PR #{monitor.pr_number} is {pr.get('state')}")
        head = self._head(pr)
        key_material = "\n".join(
            [task_id, str(submission.delivery_id), submission.assistant_message_hash, submission.decision.value, head]
        )
        digest = _sha256(key_material)
        event = self._event_from_submission(submission, context, monitor, pr, digest)
        marker = _event_marker(digest)
        body = _event_comment(event, marker)
        created, row = self.db.create_outbox(
            decision_key=digest,
            delivery_id=submission.delivery_id,
            task_id=task_id,
            actor_role=submission.role,
            conversation_key=submission.conversation_key,
            assistant_message_id=submission.assistant_message_id,
            assistant_message_hash=submission.assistant_message_hash,
            decision=submission.decision.value,
            summary=submission.summary,
            event_id=event.event_id,
            event_marker=marker,
            event_payload_json=event.model_dump_json(),
            comment_body=body,
            waiting_for_human=False,
        )
        published = self.publish(int(row["id"]))
        return {"ok": True, "created": created, "waiting_for_human": False, "outbox": published}

    def _find_existing_comment(self, pr_number: int, marker: str) -> dict[str, Any] | None:
        needle = f"<!-- SAT2_RELAY_AUTO_EVENT:{marker} -->"
        for comment in self.service.github.list_issue_comments(self.service.repo_config.repository, pr_number):
            if needle in str(comment.get("body") or ""):
                return comment
        return None

    def _revalidate_outbox(self, row: dict[str, Any]) -> tuple[RelayEvent, RepoMonitor, dict[str, Any]]:
        event = RelayEvent.model_validate_json(str(row["event_payload_json"]))
        monitor = self._monitor(event.task_id)
        state = self.db.task_state(event.task_id)
        if event.event_type is EventType.TASK_AUTHORIZED:
            if state and str(state["state"]) not in {"READY", "DORMANT"}:
                raise DecisionError("TASK_ALREADY_ACTIVE", f"Task {event.task_id} is already {state['state']}")
        elif not state or str(state["last_event_id"]) != str(event.parent_event_id or ""):
            raise DecisionError(
                "PARENT_EVENT_MISMATCH",
                f"Current parent is {state['last_event_id'] if state else None}, event expects {event.parent_event_id}",
            )
        pr = self.service.github.get_pull_request(self.service.repo_config.repository, monitor.pr_number)
        if pr.get("state") != "open":
            raise DecisionError("PR_NOT_OPEN", f"PR #{monitor.pr_number} is {pr.get('state')}")
        head = self._head(pr)
        if event.control_head_sha != head:
            raise DecisionError("STALE_PR_HEAD", f"Event head {event.control_head_sha} differs from current PR head {head}")
        if event.event_type in {EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED} and event.reviewed_sha != head:
            raise DecisionError("STALE_PR_HEAD", f"Reviewed SHA {event.reviewed_sha} differs from current PR head {head}")
        if event.event_type is EventType.WORKER_CHECKPOINT and event.candidate_sha != head:
            raise DecisionError("STALE_PR_HEAD", f"Candidate SHA {event.candidate_sha} differs from current PR head {head}")
        return event, monitor, pr

    def publish(self, outbox_id: int) -> dict[str, Any]:
        row = self.db.outbox_row(outbox_id)
        if not row:
            raise DecisionError("OUTBOX_NOT_FOUND", f"Outbox {outbox_id} does not exist")
        if row["status"] == OutboxStatus.PUBLISHED.value:
            return row
        if row["status"] == OutboxStatus.WAITING_FOR_HUMAN.value:
            raise DecisionError("WAITING_FOR_HUMAN", "Legacy outbox is waiting for local confirmation")
        if not self.service.local.allow_github_writes:
            self.db.mark_outbox_error(
                outbox_id, OutboxStatus.BLOCKED, "GITHUB_WRITES_DISABLED", "github.allow_writes is false"
            )
            raise DecisionError("GITHUB_WRITES_DISABLED", "Enable github.allow_writes in the local Relay config")
        self._config()
        event, monitor, _pr = self._revalidate_outbox(row)
        existing = self._find_existing_comment(monitor.pr_number, str(row["event_marker"]))
        if existing:
            self.db.mark_outbox_published(outbox_id, int(existing["id"]), str(existing.get("html_url") or ""))
            return self.db.outbox_row(outbox_id) or row
        if not self.db.mark_outbox_publishing(outbox_id):
            latest = self.db.outbox_row(outbox_id)
            if latest and latest["status"] == OutboxStatus.PUBLISHED.value:
                return latest
            raise DecisionError("OUTBOX_NOT_READY", f"Outbox {outbox_id} is not publishable")
        try:
            result = self.service.github.create_issue_comment(event.repository, event.pr_number, str(row["comment_body"]))
        except GitHubError as exc:
            status = OutboxStatus.PUBLISH_UNCERTAIN if exc.retryable else OutboxStatus.BLOCKED
            self.db.mark_outbox_error(outbox_id, status, "GITHUB_PUBLISH_FAILED", str(exc), 10)
            raise DecisionError("GITHUB_PUBLISH_FAILED", str(exc)) from exc
        except Exception as exc:
            self.db.mark_outbox_error(
                outbox_id, OutboxStatus.PUBLISH_UNCERTAIN, "GITHUB_PUBLISH_UNCERTAIN", str(exc), 10
            )
            raise DecisionError("GITHUB_PUBLISH_UNCERTAIN", str(exc)) from exc
        self.db.mark_outbox_published(outbox_id, int(result["id"]), str(result.get("html_url") or ""))
        return self.db.outbox_row(outbox_id) or row

    def recover(self, limit: int = 20) -> dict[str, int]:
        counts = {"examined": 0, "published": 0, "blocked": 0, "retry": 0}
        for row in self.db.ready_outbox(limit):
            counts["examined"] += 1
            try:
                result = self.publish(int(row["id"]))
                if result.get("status") == OutboxStatus.PUBLISHED.value:
                    counts["published"] += 1
            except DecisionError as exc:
                if exc.code in {
                    "STALE_PR_HEAD",
                    "PARENT_EVENT_MISMATCH",
                    "PR_NOT_OPEN",
                    "GITHUB_WRITES_DISABLED",
                    "TASK_ALREADY_ACTIVE",
                }:
                    counts["blocked"] += 1
                else:
                    counts["retry"] += 1
        return counts

    def confirm(self, outbox_id: int) -> dict[str, Any]:
        # Backward compatibility for old 2.2 outboxes only. New document-driven
        # Mentor Accepted decisions are published automatically.
        if not self.db.confirm_outbox(outbox_id):
            row = self.db.outbox_row(outbox_id)
            if row and row["status"] == OutboxStatus.PUBLISHED.value:
                return row
            raise DecisionError("OUTBOX_NOT_WAITING", "Outbox is not waiting for human confirmation")
        return self.publish(outbox_id)

    def _ensure_no_control_history_before_root(self, monitor: RepoMonitor) -> None:
        """Do not create a second protocol root when GitHub already has control history.

        A fresh/rebuilt local database can have no task_state while the PR still
        contains valid or malformed Relay events after the configured start
        boundary. Polling must reconstruct/reject that history first. Automatic
        document dispatch is allowed only when that portion of GitHub is clean.
        """
        comments = self.service.github.list_issue_comments(self.service.repo_config.repository, monitor.pr_number)
        for comment in comments:
            comment_id = int(comment.get("id") or 0)
            if monitor.start_after_comment_id and comment_id <= monitor.start_after_comment_id:
                continue
            body = str(comment.get("body") or "")
            if "SAT2_RELAY_EVENT_V" in body or "```sat2-relay" in body:
                raise DecisionError(
                    "CONTROL_HISTORY_PENDING",
                    f"PR #{monitor.pr_number} already contains Relay control history after the monitor start boundary "
                    f"(comment {comment_id}); poll/reconcile that chain before creating a document-dispatch root.",
                )

    def preview_document_dispatch(self, task_id: str) -> dict[str, Any]:
        config = self._config()
        monitor = self._monitor(task_id)
        state = self.db.task_state(task_id)
        if state and str(state["state"]) not in {"READY", "DORMANT"}:
            raise DecisionError("TASK_ALREADY_ACTIVE", f"Task {task_id} is already {state['state']}")
        if not state:
            self._ensure_no_control_history_before_root(monitor)
        pr = self.service.github.get_pull_request(config.repository, monitor.pr_number)
        if pr.get("state") != "open":
            raise DecisionError("PR_NOT_OPEN", f"PR #{monitor.pr_number} is {pr.get('state')}")
        task_spec = self.service.load_task_spec(monitor, pr)
        self.service.validate_task_spec_ready(monitor, pr, task_spec)
        self.service._validate_dependencies(monitor)
        self.service._validate_scope_conflicts(monitor)
        head = self._head(pr)
        base = self._base(pr)
        summary = (
            f"Mentor-authored task document {monitor.task_file} is executable and complete enough for automatic dispatch; "
            f"Relay is starting {task_id} on {monitor.worker_role} without an additional human authorization step."
        )
        key_material = "\n".join([task_id, "DOCUMENT_DISPATCH", task_spec.sha256, base, head])
        digest = _sha256(key_material)
        event = RelayEvent.model_validate(
            {
                "protocol": "sat2-relay/v2",
                # Retain SAT2_TASK_AUTHORIZED on the wire for v2 compatibility.
                # In 2.2.2 it is a deterministic document-dispatch root event,
                # not a separate user authorization gate.
                "event_id": _event_id(task_id, digest),
                "event_type": EventType.TASK_AUTHORIZED.value,
                "repository": config.repository,
                "task_id": task_id,
                "actor_role": "mentor",
                "target_role": monitor.worker_role,
                "pr_number": monitor.pr_number,
                "base_sha": base,
                "authorized_sha": head,
                "control_head_sha": head,
                "correlation_id": _event_id(task_id, digest),
                "task_spec_sha256": task_spec.sha256,
                "attempt": self.db.next_event_attempt(task_id, EventType.TASK_AUTHORIZED.value),
                "timestamp": datetime.now(UTC),
                "summary": summary,
            }
        )
        commits = {
            str(row.get("sha") or "")
            for row in self.service.github.list_pull_request_commits(config.repository, monitor.pr_number)
        }
        validate_actor_semantics(event, monitor)
        validate_pr_binding(event, pr, commits)
        validate_transition(event, state["state"] if state else None, state["last_event_id"] if state else None)
        marker = _event_marker(digest)
        return {
            "task_id": task_id,
            "pr_number": monitor.pr_number,
            "base_sha": base,
            "head_sha": head,
            "worker_role": monitor.worker_role,
            "task_spec": monitor.task_file,
            "task_spec_sha256": task_spec.sha256,
            "event": event.model_dump(mode="json", exclude_none=True),
            "event_marker": marker,
            "comment_body": _event_comment(event, marker),
            "decision_key": digest,
        }

    def dispatch_document(self, task_id: str) -> dict[str, Any]:
        state = self.db.task_state(task_id)
        if state and str(state["state"]) not in {"READY", "DORMANT"}:
            return {"ok": True, "created": False, "skipped": "task_already_has_protocol_state", "state": dict(state)}
        preview = self.preview_document_dispatch(task_id)
        event = RelayEvent.model_validate(preview["event"])
        created, row = self.db.create_outbox(
            decision_key=str(preview["decision_key"]),
            delivery_id=None,
            task_id=task_id,
            actor_role="mentor",
            conversation_key=None,
            assistant_message_id=None,
            assistant_message_hash=None,
            decision="DOCUMENT_DISPATCH",
            summary=str(event.summary or "Mentor task document dispatch"),
            event_id=event.event_id,
            event_marker=str(preview["event_marker"]),
            event_payload_json=event.model_dump_json(),
            comment_body=str(preview["comment_body"]),
            waiting_for_human=False,
        )
        result = self.publish(int(row["id"]))
        return {"ok": True, "created": created, "outbox": result}

    # Backward-compatible API names. They no longer represent a required human
    # gate; normal operation uses dispatch_document() automatically from poll.
    def preview_authorization(self, task_id: str, summary: str) -> dict[str, Any]:
        _ = summary
        return self.preview_document_dispatch(task_id)

    def authorize(self, task_id: str, summary: str) -> dict[str, Any]:
        _ = summary
        return self.dispatch_document(task_id)
