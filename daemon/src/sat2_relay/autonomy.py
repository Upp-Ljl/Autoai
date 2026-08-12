from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import (
    DecisionName,
    DecisionSubmission,
    EventType,
    RelayEvent,
    RelayMode,
    RepoMonitor,
    RepoRoute,
    RouteSignalMode,
    SHA256_PATTERN,
    SHA_PATTERN,
    normalize_session_role,
)
from .protocol import (
    build_execution_capsule,
    delivery_marker,
    resolve_target,
    transition_name,
    validate_actor_semantics,
    validate_pr_binding,
    validate_transition,
)


class ProgressEventType(StrEnum):
    ROUTE_INIT = "ROUTE_INIT"
    WORKER_CHECKPOINT = "WORKER_CHECKPOINT"
    MENTOR_CHANGES_REQUIRED = "MENTOR_CHANGES_REQUIRED"
    MENTOR_ACCEPTED = "MENTOR_ACCEPTED"
    TASK_BLOCKED = "TASK_BLOCKED"


class RouteStatus(StrEnum):
    ACTIVE = "ACTIVE"
    BLOCKED = "BLOCKED"
    COMPLETE = "COMPLETE"


class ProgressDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(default=2, alias="schema", ge=2, le=2)
    route: str = Field(pattern=r"^[A-Za-z0-9._-]+$")
    handoff_sequence: int = Field(ge=0)
    parent_sequence: int | None = Field(default=None, ge=0)
    event_type: ProgressEventType
    route_status: RouteStatus = RouteStatus.ACTIVE
    stage: int = Field(default=0, ge=0, le=10000)
    updated_by: str
    updated_at: datetime
    current_task: str | None = Field(default=None, max_length=320)
    task_id: str | None = Field(default=None, max_length=160)
    next_task: str | None = Field(default=None, max_length=320)
    pr_number: int | None = Field(default=None, gt=0)
    candidate_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    reviewed_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    control_head_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    task_contract_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    last_summary: str = Field(default="", max_length=800)

    @field_validator("updated_by")
    @classmethod
    def normalize_role(cls, value: str) -> str:
        return normalize_session_role(value)

    @field_validator("current_task", "next_task")
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = value.strip().replace("\\", "/").lstrip("/")
        parts = [part for part in path.split("/") if part]
        if not parts or any(part in {".", ".."} for part in parts):
            raise ValueError("progress task paths must be normalized relative paths")
        return "/".join(parts)

    @model_validator(mode="after")
    def validate_sequence_shape(self) -> "ProgressDocument":
        if self.handoff_sequence == 0:
            if self.event_type is not ProgressEventType.ROUTE_INIT:
                raise ValueError("sequence 0 must use ROUTE_INIT")
            if self.parent_sequence is not None:
                raise ValueError("ROUTE_INIT must use parent_sequence: null")
            return self
        if self.event_type is ProgressEventType.ROUTE_INIT:
            raise ValueError("ROUTE_INIT is only valid at sequence 0")
        if self.parent_sequence != self.handoff_sequence - 1:
            raise ValueError("parent_sequence must equal handoff_sequence - 1")
        if not self.current_task or not self.task_id or not self.pr_number:
            raise ValueError("handoff events require current_task, task_id and pr_number")
        if not self.control_head_sha or not self.task_contract_sha256:
            raise ValueError("handoff events require control_head_sha and task_contract_sha256")
        if self.event_type is ProgressEventType.WORKER_CHECKPOINT and not self.candidate_sha:
            raise ValueError("WORKER_CHECKPOINT requires candidate_sha")
        if self.event_type in {ProgressEventType.MENTOR_CHANGES_REQUIRED, ProgressEventType.MENTOR_ACCEPTED} and not self.reviewed_sha:
            raise ValueError(f"{self.event_type.value} requires reviewed_sha")
        if self.event_type is ProgressEventType.MENTOR_ACCEPTED:
            if self.route_status is RouteStatus.COMPLETE:
                if self.next_task is not None:
                    raise ValueError("completed route must use next_task: null")
            elif not self.next_task:
                raise ValueError("MENTOR_ACCEPTED requires next_task unless route_status is COMPLETE")
        if self.event_type is ProgressEventType.TASK_BLOCKED and self.route_status is not RouteStatus.BLOCKED:
            raise ValueError("TASK_BLOCKED requires route_status: BLOCKED")
        return self


class ProgressError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


_DECISION_PROGRESS = {
    DecisionName.WORKER_CHECKPOINT: ProgressEventType.WORKER_CHECKPOINT,
    DecisionName.MENTOR_CHANGES_REQUIRED: ProgressEventType.MENTOR_CHANGES_REQUIRED,
    DecisionName.MENTOR_ACCEPTED: ProgressEventType.MENTOR_ACCEPTED,
    DecisionName.TASK_BLOCKED: ProgressEventType.TASK_BLOCKED,
}

_PROGRESS_EVENT = {
    ProgressEventType.WORKER_CHECKPOINT: EventType.WORKER_CHECKPOINT,
    ProgressEventType.MENTOR_CHANGES_REQUIRED: EventType.MENTOR_CHANGES_REQUIRED,
    ProgressEventType.MENTOR_ACCEPTED: EventType.MENTOR_ACCEPTED,
    ProgressEventType.TASK_BLOCKED: EventType.TASK_BLOCKED,
}


def _safe_id(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)[:100]


class ParallelAutonomyController:
    """Route-local progress control plane.

    GitHub progress documents carry route state and exact scientific bindings.
    Session-bound Decision submissions are the identity attestation. A progress
    handoff is never routed solely because YAML claims ``updated_by``.
    """

    def __init__(self, service: Any, db: Any):
        self.service = service
        self.db = db

    @property
    def config(self):
        if not self.service.repo_config:
            self.service.refresh_config()
        return self.service.repo_config

    def route(self, route_id: str) -> RepoRoute:
        for route in self.config.routes:
            if route.enabled and route.route_id == route_id:
                return route
        raise ProgressError("ROUTE_NOT_CONFIGURED", f"No enabled route {route_id}")

    def route_for_monitor(self, monitor: RepoMonitor) -> RepoRoute | None:
        if not monitor.route_id:
            return None
        try:
            return self.route(monitor.route_id)
        except ProgressError:
            return None

    def is_progress_monitor(self, monitor: RepoMonitor) -> bool:
        route = self.route_for_monitor(monitor)
        return bool(route and route.signal_mode is RouteSignalMode.PROGRESS)

    def _resolve_ref(self, value: str | None, fallback: str) -> str | None:
        value = (value or fallback).strip()
        if value == "@config":
            return self.service.local.repository_config_ref
        if value == "@default":
            return None
        if value in {"@pr-head", "@pr-base"}:
            raise ProgressError("ROUTE_REF_INVALID", f"route control ref {value} is not stable enough for autonomy")
        return value

    def progress_ref(self, route: RepoRoute) -> str | None:
        return self._resolve_ref(route.progress_ref, self.service.local.repository_config_ref)

    def task_ref(self, route: RepoRoute) -> str | None:
        return self._resolve_ref(route.task_ref, route.progress_ref)

    @staticmethod
    def _meta(route: RepoRoute, suffix: str) -> str:
        return f"route:{route.route_id}:{suffix}"

    @staticmethod
    def _runtime_key(task_id: str) -> str:
        return f"runtime_monitor:{task_id}"

    def runtime_monitor(self, task_id: str) -> RepoMonitor | None:
        raw = self.db.get_meta(self._runtime_key(task_id))
        if not raw:
            return None
        try:
            return RepoMonitor.model_validate_json(raw)
        except Exception as exc:
            raise ProgressError("RUNTIME_MONITOR_CORRUPT", f"runtime monitor {task_id}: {exc}") from exc

    def monitor_for_task(self, task_id: str) -> RepoMonitor | None:
        for monitor in self.config.monitors:
            if monitor.enabled and monitor.task_id == task_id:
                return monitor
        return self.runtime_monitor(task_id)

    def _save_runtime_monitor(self, monitor: RepoMonitor) -> None:
        self.db.set_meta(self._runtime_key(monitor.task_id), monitor.model_dump_json())

    def _path_in_root(self, route: RepoRoute, path: str) -> bool:
        normalized = PurePosixPath(path).as_posix().lstrip("/")
        root = PurePosixPath(route.task_root).as_posix().rstrip("/")
        return normalized == root or normalized.startswith(root + "/")

    def _load_progress(self, route: RepoRoute) -> tuple[ProgressDocument, str]:
        ref = self.progress_ref(route)
        try:
            text = self.service.github.get_content_text(self.config.repository, route.progress_file, ref)
        except Exception as exc:
            raise ProgressError(
                "PROGRESS_UNAVAILABLE",
                f"{route.route_id}: cannot read {route.progress_file}@{ref or '<default>'}: {exc}",
            ) from exc
        try:
            raw = yaml.safe_load(text)
            if not isinstance(raw, dict):
                raise ValueError("document is not a mapping")
            doc = ProgressDocument.model_validate(raw)
        except Exception as exc:
            raise ProgressError("PROGRESS_INVALID", f"{route.route_id}: invalid progress document: {exc}") from exc
        if doc.route != route.route_id:
            raise ProgressError("PROGRESS_ROUTE_MISMATCH", f"progress route {doc.route} != {route.route_id}")
        return doc, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _load_task_monitor(self, route: RepoRoute, task_file: str) -> tuple[RepoMonitor, Any]:
        if not self._path_in_root(route, task_file):
            raise ProgressError("NEXT_TASK_OUTSIDE_ROUTE", f"{task_file} is outside {route.task_root}")
        ref = self.task_ref(route)
        try:
            text = self.service.github.get_content_text(self.config.repository, task_file, ref)
            doc = yaml.safe_load(text)
        except Exception as exc:
            raise ProgressError("NEXT_TASK_UNAVAILABLE", f"cannot load next task {task_file}@{ref}: {exc}") from exc
        if not isinstance(doc, dict):
            raise ProgressError("NEXT_TASK_INVALID", f"task {task_file} is not a YAML mapping")
        task_id = str(doc.get("task_id") or "").strip()
        if not task_id:
            raise ProgressError("NEXT_TASK_INVALID", f"task {task_file} has no task_id")
        doc_worker = normalize_session_role(str(doc.get("worker_role") or route.worker_role))
        if doc_worker != route.worker_role:
            raise ProgressError("NEXT_TASK_ROLE_MISMATCH", f"task {task_id} worker {doc_worker} != route worker {route.worker_role}")
        doc_mentor = normalize_session_role(str(doc.get("mentor_role") or route.mentor_role))
        if doc_mentor != route.mentor_role:
            raise ProgressError("NEXT_TASK_ROLE_MISMATCH", f"task {task_id} mentor {doc_mentor} != route mentor {route.mentor_role}")
        if doc.get("route_id") and str(doc["route_id"]) != route.route_id:
            raise ProgressError("NEXT_TASK_ROUTE_MISMATCH", f"task {task_id} route_id differs from {route.route_id}")
        allowed = [str(value) for value in (doc.get("allowed_paths") or [])]
        forbidden = [str(value) for value in (doc.get("forbidden_paths") or [])]
        dependencies = [str(value) for value in (doc.get("dependencies") or [])]
        monitor = RepoMonitor(
            pr_number=route.pr_number,
            task_id=task_id,
            worker_role=route.worker_role,
            mentor_role=route.mentor_role,
            route_id=route.route_id,
            signal_mode=route.signal_mode,
            progress_file=route.progress_file,
            progress_ref=str(self.progress_ref(route) or "@default"),
            required_apps=route.required_apps,
            strict_apps=route.strict_apps,
            task_file=task_file,
            task_ref=str(ref or "@default"),
            allowed_paths=allowed,
            forbidden_paths=forbidden,
            dependencies=dependencies,
        )
        pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
        if pr.get("state") != "open":
            raise ProgressError("PR_NOT_OPEN", f"Route {route.route_id} PR #{route.pr_number} is {pr.get('state')}")
        resolved = self.service.load_task_spec(monitor, pr)
        try:
            self.service.validate_task_spec_ready(monitor, pr, resolved)
        except Exception as exc:
            raise ProgressError("NEXT_TASK_INVALID", f"task {task_id} is not executable: {exc}") from exc
        return monitor, resolved

    def _event_id(self, route: RepoRoute, doc: ProgressDocument) -> str:
        return f"progress.{_safe_id(route.route_id)}.{doc.handoff_sequence}.{_safe_id(doc.task_id or 'init')}"

    def _root_event_id(self, route: RepoRoute, monitor: RepoMonitor, contract_sha: str, head: str) -> str:
        digest = hashlib.sha256(
            "\n".join([route.route_id, monitor.task_id, contract_sha, head, "PROGRESS_DOCUMENT_DISPATCH"]).encode("utf-8")
        ).hexdigest()
        return f"{_safe_id(route.route_id)}.{_safe_id(monitor.task_id)}.root.{digest[:24]}"

    def _dispatch_root(self, route: RepoRoute, monitor: RepoMonitor, resolved: Any | None = None) -> dict[str, Any]:
        pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
        if pr.get("state") != "open":
            raise ProgressError("PR_NOT_OPEN", f"Route {route.route_id} PR #{route.pr_number} is {pr.get('state')}")
        resolved = resolved or self.service.load_task_spec(monitor, pr)
        self.service.validate_task_spec_ready(monitor, pr, resolved)
        self.service._validate_dependencies(monitor)
        head = str((pr.get("head") or {}).get("sha") or "")
        base = str((pr.get("base") or {}).get("sha") or "")
        event_id = self._root_event_id(route, monitor, resolved.sha256, head)
        state = self.db.task_state(monitor.task_id)
        if state and str(state["state"]) not in {"READY", "DORMANT"}:
            self._save_runtime_monitor(monitor)
            self.db.set_meta(self._meta(route, "current_task_id"), monitor.task_id)
            self.db.set_meta(self._meta(route, "current_task_file"), str(monitor.task_file or ""))
            return {"created": False, "task_id": monitor.task_id, "state": str(state["state"])}
        event = RelayEvent.model_validate(
            {
                "protocol": "sat2-relay/v2",
                "event_id": event_id,
                "event_type": EventType.TASK_AUTHORIZED.value,
                "repository": self.config.repository,
                "task_id": monitor.task_id,
                "actor_role": route.mentor_role,
                "target_role": route.worker_role,
                "pr_number": route.pr_number,
                "base_sha": base,
                "authorized_sha": head,
                "control_head_sha": head,
                "correlation_id": event_id,
                "task_spec_sha256": resolved.sha256,
                "attempt": self.db.next_event_attempt(monitor.task_id, EventType.TASK_AUTHORIZED.value),
                "timestamp": datetime.now(UTC),
                "summary": f"Route {route.route_id} Mentor-authored task document {monitor.task_file} is executable; deterministic progress-root dispatch.",
                "source_comment_id": 0,
                "source_actor": "route-config",
            }
        )
        validate_actor_semantics(event, monitor)
        commits = {
            str(row.get("sha") or "")
            for row in self.service.github.list_pull_request_commits(self.config.repository, route.pr_number)
        }
        validate_pr_binding(event, pr, commits)
        validate_transition(event, state["state"] if state else None, state["last_event_id"] if state else None)
        target = route.worker_role
        token = secrets.token_urlsafe(24)
        capsule = build_execution_capsule(
            event,
            target,
            delivery_marker(event, target),
            monitor,
            token,
            resolved.document,
            resolved.sha256,
        )
        mode = self.service.effective_mode()
        if mode is RelayMode.SHADOW:
            return {"created": False, "task_id": monitor.task_id, "shadow": True}
        inserted, delivery_id = self.db.accept_event(
            event,
            event.model_dump_json(),
            new_state=transition_name(event),
            worker_role=monitor.worker_role,
            state_sha=head,
            target_role=target,
            body=capsule,
            delivery_token=token,
            required_apps=monitor.required_apps,
            strict_apps=monitor.strict_apps,
            awaiting_approval=mode is RelayMode.DRY_RUN,
        )
        self._save_runtime_monitor(monitor)
        self.db.set_meta(f"task_contract:{monitor.task_id}:sha256", resolved.sha256)
        self.db.set_meta(f"task_contract:{monitor.task_id}:path", str(monitor.task_file or ""))
        self.db.set_meta(f"task_contract:{monitor.task_id}:ref", str(monitor.task_ref))
        self.db.set_meta(self._meta(route, "current_task_id"), monitor.task_id)
        self.db.set_meta(self._meta(route, "current_task_file"), str(monitor.task_file or ""))
        self.db.set_meta(self._meta(route, "status"), RouteStatus.ACTIVE.value)
        return {"created": inserted, "task_id": monitor.task_id, "delivery_id": delivery_id}

    def _bootstrap(self, route: RepoRoute) -> dict[str, Any] | None:
        if route.signal_mode is not RouteSignalMode.PROGRESS or not route.bootstrap_task_file:
            return None
        if self.db.get_meta(self._meta(route, "current_task_id")):
            return None
        monitor, resolved = self._load_task_monitor(route, route.bootstrap_task_file)
        return self._dispatch_root(route, monitor, resolved)

    def _processed_sequence(self, route: RepoRoute) -> int | None:
        raw = self.db.get_meta(self._meta(route, "processed_sequence"))
        return int(raw) if raw is not None else None

    def _validate_stage(self, route: RepoRoute, doc: ProgressDocument) -> None:
        raw = self.db.get_meta(self._meta(route, "stage"))
        if raw is None:
            return
        previous = int(raw)
        if doc.event_type is ProgressEventType.MENTOR_ACCEPTED and doc.route_status is not RouteStatus.COMPLETE:
            if doc.stage != previous + 1:
                raise ProgressError("PROGRESS_STAGE_INVALID", f"accepted next task must advance stage {previous} -> {previous + 1}, got {doc.stage}")
        elif doc.stage != previous:
            raise ProgressError("PROGRESS_STAGE_INVALID", f"{doc.event_type.value} must preserve stage {previous}, got {doc.stage}")

    def _advance_progress_meta(self, route: RepoRoute, doc: ProgressDocument, digest: str) -> None:
        self.db.set_meta(self._meta(route, "processed_sequence"), str(doc.handoff_sequence))
        self.db.set_meta(self._meta(route, "stage"), str(doc.stage))
        self.db.set_meta(self._meta(route, "progress_sha256"), digest)
        self.db.set_meta(self._meta(route, "status"), doc.route_status.value)

    def _recover_accepted_next(self, route: RepoRoute, doc: ProgressDocument) -> None:
        if doc.event_type is not ProgressEventType.MENTOR_ACCEPTED or doc.route_status is RouteStatus.COMPLETE:
            return
        if not doc.next_task:
            raise ProgressError("NEXT_TASK_MISSING", "accepted route handoff has no next_task")
        monitor, resolved = self._load_task_monitor(route, doc.next_task)
        self._dispatch_root(route, monitor, resolved)

    def observe_route(self, route: RepoRoute) -> dict[str, Any]:
        doc, digest = self._load_progress(route)
        if route.signal_mode is RouteSignalMode.PROGRESS_SHADOW:
            self.db.set_meta(self._meta(route, "shadow_sequence"), str(doc.handoff_sequence))
            self.db.set_meta(self._meta(route, "shadow_sha256"), digest)
            return {"route": route.route_id, "shadow": True, "sequence": doc.handoff_sequence}
        if route.signal_mode is not RouteSignalMode.PROGRESS:
            return {"route": route.route_id, "ignored": True}

        processed = self._processed_sequence(route)
        if processed is None:
            if doc.handoff_sequence != 0 or doc.event_type is not ProgressEventType.ROUTE_INIT:
                raise ProgressError(
                    "PROGRESS_BASELINE_REQUIRED",
                    f"route {route.route_id} must start from sequence 0 ROUTE_INIT, got {doc.handoff_sequence} {doc.event_type.value}",
                )
            if doc.updated_by not in {route.mentor_role, route.worker_role}:
                raise ProgressError("PROGRESS_ROLE_INVALID", f"ROUTE_INIT updated_by {doc.updated_by} is outside route roles")
            self._advance_progress_meta(route, doc, digest)
            processed = 0
        elif doc.handoff_sequence < processed:
            raise ProgressError("PROGRESS_SEQUENCE_ROLLBACK", f"route {route.route_id} sequence rolled back {processed} -> {doc.handoff_sequence}")
        elif doc.handoff_sequence > processed + 1:
            raise ProgressError("PROGRESS_SEQUENCE_GAP", f"route {route.route_id} sequence gap {processed} -> {doc.handoff_sequence}")
        elif doc.handoff_sequence == processed + 1:
            event_id = self._event_id(route, doc)
            if self.db.event_payload(event_id):
                self._recover_accepted_next(route, doc)
                self._advance_progress_meta(route, doc, digest)
            else:
                self.db.set_meta(self._meta(route, "pending_sequence"), str(doc.handoff_sequence))
                self.db.set_meta(self._meta(route, "pending_sha256"), digest)
        self._bootstrap(route)
        return {"route": route.route_id, "sequence": doc.handoff_sequence, "processed": self._processed_sequence(route)}

    def poll_routes(self) -> dict[str, int]:
        counts = {"routes": 0, "route_errors": 0, "route_bootstraps": 0}
        for route in self.config.routes:
            if not route.enabled or route.signal_mode is RouteSignalMode.COMMENT:
                continue
            counts["routes"] += 1
            try:
                before = self.db.get_meta(self._meta(route, "current_task_id"))
                self.observe_route(route)
                after = self.db.get_meta(self._meta(route, "current_task_id"))
                if not before and after:
                    counts["route_bootstraps"] += 1
                self.db.resolve_alerts(code="PROGRESS_INVALID", pr_number=route.pr_number)
                self.db.resolve_alerts(code="PROGRESS_SEQUENCE_GAP", pr_number=route.pr_number)
            except ProgressError as exc:
                counts["route_errors"] += 1
                self.service._alert(
                    "error",
                    exc.code,
                    exc.detail,
                    self.db.get_meta(self._meta(route, "current_task_id")),
                    route.pr_number,
                    60,
                )
            except Exception as exc:
                counts["route_errors"] += 1
                self.service._alert(
                    "error",
                    "PROGRESS_ROUTE_FAILED",
                    f"{route.route_id}: {exc}",
                    self.db.get_meta(self._meta(route, "current_task_id")),
                    route.pr_number,
                    60,
                )
        return counts

    def _validate_attested_progress(
        self,
        route: RepoRoute,
        monitor: RepoMonitor,
        submission: DecisionSubmission,
    ) -> tuple[ProgressDocument, str, dict[str, Any], Any]:
        if submission.decision is DecisionName.WORKER_ACK:
            raise ProgressError("PROGRESS_ACK_NOT_USED", "WORKER_ACK is transport-only and does not advance handoff_sequence")
        expected_type = _DECISION_PROGRESS.get(submission.decision)
        if not expected_type:
            raise ProgressError("PROGRESS_DECISION_INVALID", f"unsupported progress decision {submission.decision.value}")
        doc, digest = self._load_progress(route)
        processed = self._processed_sequence(route)
        if processed is None:
            raise ProgressError("PROGRESS_BASELINE_REQUIRED", "route progress has not been baselined at sequence 0")
        if doc.handoff_sequence == processed:
            if doc.event_type is expected_type and self.db.event_payload(self._event_id(route, doc)):
                pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
                return doc, digest, pr, self.service.load_task_spec(monitor, pr)
            raise ProgressError("PROGRESS_NOT_ADVANCED", f"route {route.route_id} progress is still sequence {processed}")
        if doc.handoff_sequence != processed + 1:
            code = "PROGRESS_SEQUENCE_GAP" if doc.handoff_sequence > processed + 1 else "PROGRESS_SEQUENCE_ROLLBACK"
            raise ProgressError(code, f"expected route sequence {processed + 1}, got {doc.handoff_sequence}")
        if doc.parent_sequence != processed:
            raise ProgressError("PROGRESS_PARENT_MISMATCH", f"expected parent_sequence {processed}, got {doc.parent_sequence}")
        if doc.event_type is not expected_type:
            raise ProgressError("PROGRESS_DECISION_MISMATCH", f"Decision {submission.decision.value} != progress event {doc.event_type.value}")
        if doc.updated_by != submission.role:
            raise ProgressError("PROGRESS_IDENTITY_MISMATCH", f"Session role {submission.role} != progress updated_by {doc.updated_by}")
        if submission.role not in {route.mentor_role, route.worker_role}:
            raise ProgressError("PROGRESS_ROLE_INVALID", f"role {submission.role} is not part of route {route.route_id}")
        if doc.task_id != monitor.task_id or doc.current_task != monitor.task_file:
            raise ProgressError(
                "PROGRESS_TASK_MISMATCH",
                f"progress task {doc.task_id}/{doc.current_task} != delivery task {monitor.task_id}/{monitor.task_file}",
            )
        if doc.pr_number != route.pr_number or monitor.pr_number != route.pr_number:
            raise ProgressError("PROGRESS_PR_MISMATCH", f"progress PR {doc.pr_number} != route PR {route.pr_number}")
        self._validate_stage(route, doc)
        pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
        if pr.get("state") != "open":
            raise ProgressError("PR_NOT_OPEN", f"Route PR #{route.pr_number} is {pr.get('state')}")
        head = str((pr.get("head") or {}).get("sha") or "")
        if doc.control_head_sha != head:
            raise ProgressError("STALE_PR_HEAD", f"progress head {doc.control_head_sha} differs from current PR head {head}")
        resolved = self.service.load_task_spec(monitor, pr)
        frozen = self.db.get_meta(f"task_contract:{monitor.task_id}:sha256")
        if not frozen or frozen != resolved.sha256 or doc.task_contract_sha256 != frozen:
            raise ProgressError(
                "TASK_CONTRACT_MISMATCH",
                f"progress/current/frozen task contract mismatch for {monitor.task_id}",
            )
        if doc.event_type is ProgressEventType.WORKER_CHECKPOINT:
            if submission.role != route.worker_role:
                raise ProgressError("PROGRESS_ROLE_INVALID", "WORKER_CHECKPOINT must be attested by route worker")
            if doc.candidate_sha != head:
                raise ProgressError("STALE_PR_HEAD", f"candidate {doc.candidate_sha} differs from current PR head {head}")
        elif doc.event_type in {ProgressEventType.MENTOR_CHANGES_REQUIRED, ProgressEventType.MENTOR_ACCEPTED}:
            if submission.role != route.mentor_role:
                raise ProgressError("PROGRESS_ROLE_INVALID", f"{doc.event_type.value} must be attested by route mentor")
            state = self.db.task_state(monitor.task_id)
            if not state or str(state["state"]) != "MENTOR_REVIEW":
                raise ProgressError("ILLEGAL_STATE_TRANSITION", f"task {monitor.task_id} is not in MENTOR_REVIEW")
            checkpoint_sha = str(state["sha"] or "")
            if doc.reviewed_sha != checkpoint_sha or doc.reviewed_sha != head:
                raise ProgressError(
                    "STALE_PR_HEAD",
                    f"reviewed {doc.reviewed_sha} != checkpoint/current head {checkpoint_sha}/{head}",
                )
        elif doc.event_type is ProgressEventType.TASK_BLOCKED:
            if doc.route_status is not RouteStatus.BLOCKED:
                raise ProgressError("PROGRESS_STATUS_INVALID", "TASK_BLOCKED requires route_status BLOCKED")
        return doc, digest, pr, resolved

    def _event_from_progress(
        self,
        route: RepoRoute,
        monitor: RepoMonitor,
        doc: ProgressDocument,
        submission: DecisionSubmission,
        pr: dict[str, Any],
    ) -> RelayEvent:
        event_type = _PROGRESS_EVENT[doc.event_type]
        state = self.db.task_state(monitor.task_id)
        parent = str(state["last_event_id"] or "") if state else None
        head = str((pr.get("head") or {}).get("sha") or "")
        parent_payload = self.db.event_payload(parent) if parent else None
        correlation = str((parent_payload or {}).get("correlation_id") or (parent_payload or {}).get("event_id") or parent or self._event_id(route, doc))
        fields: dict[str, Any] = {
            "protocol": "sat2-relay/v2",
            "event_id": self._event_id(route, doc),
            "event_type": event_type.value,
            "repository": self.config.repository,
            "task_id": monitor.task_id,
            "actor_role": submission.role,
            "target_role": resolve_target(
                RelayEvent.model_construct(event_type=event_type, actor_role=submission.role), monitor
            ),
            "pr_number": route.pr_number,
            "parent_event_id": parent,
            "correlation_id": correlation,
            "control_head_sha": head,
            "task_spec_sha256": doc.task_contract_sha256,
            "attempt": self.db.next_event_attempt(monitor.task_id, event_type.value),
            "timestamp": datetime.now(UTC),
            "summary": submission.summary,
            "source_comment_id": 0,
            "source_actor": f"progress:{route.route_id}:{submission.role}",
        }
        if event_type is EventType.WORKER_CHECKPOINT:
            fields["candidate_sha"] = doc.candidate_sha
        elif event_type in {EventType.MENTOR_CHANGES_REQUIRED, EventType.MENTOR_ACCEPTED}:
            fields["reviewed_sha"] = doc.reviewed_sha
            fields["next_task"] = doc.next_task
            if event_type is EventType.MENTOR_ACCEPTED:
                fields["target_role"] = None
        elif event_type is EventType.TASK_BLOCKED:
            fields["target_role"] = None
        event = RelayEvent.model_validate(fields)
        validate_actor_semantics(event, monitor)
        commits = {
            str(row.get("sha") or "")
            for row in self.service.github.list_pull_request_commits(self.config.repository, route.pr_number)
        }
        validate_pr_binding(event, pr, commits)
        validate_transition(event, state["state"] if state else None, state["last_event_id"] if state else None)
        changed = [
            str(row.get("filename") or "")
            for row in self.service.github.list_pull_request_files(self.config.repository, route.pr_number)
        ]
        self.service._validate_monitor_contract(monitor, pr, changed, event)
        return event

    def _record_local_decision(
        self,
        submission: DecisionSubmission,
        event: RelayEvent,
        route: RepoRoute,
    ) -> dict[str, Any]:
        key = hashlib.sha256(
            "\n".join(
                [
                    route.route_id,
                    event.event_id,
                    str(submission.delivery_id),
                    submission.assistant_message_hash,
                    submission.decision.value,
                ]
            ).encode("utf-8")
        ).hexdigest()
        created, row = self.db.create_outbox(
            decision_key=f"progress:{key}",
            delivery_id=submission.delivery_id,
            task_id=event.task_id,
            actor_role=submission.role,
            conversation_key=submission.conversation_key,
            assistant_message_id=submission.assistant_message_id,
            assistant_message_hash=submission.assistant_message_hash,
            decision=submission.decision.value,
            summary=submission.summary,
            event_id=event.event_id,
            event_marker=f"progress-{route.route_id}-{key[:32]}",
            event_payload_json=event.model_dump_json(),
            comment_body=f"progress://{route.route_id}/{event.event_id}",
            waiting_for_human=False,
        )
        if row.get("status") != "published":
            self.db.mark_outbox_published(int(row["id"]), 0, f"progress://{route.route_id}/{event.event_id}")
            row = self.db.outbox_row(int(row["id"])) or row
        return {"created": created, "audit": row}

    def submit_decision(
        self,
        submission: DecisionSubmission,
        context: dict[str, Any],
        monitor: RepoMonitor,
    ) -> dict[str, Any]:
        route = self.route_for_monitor(monitor)
        if not route or route.signal_mode is not RouteSignalMode.PROGRESS:
            raise ProgressError("ROUTE_NOT_PROGRESS_ACTIVE", f"task {monitor.task_id} is not on an active progress route")
        doc, digest, pr, resolved = self._validate_attested_progress(route, monitor, submission)
        event_id = self._event_id(route, doc)
        existing_event = self.db.event_payload(event_id)
        if existing_event:
            event = RelayEvent.model_validate(existing_event)
            local = self._record_local_decision(submission, event, route)
            self._recover_accepted_next(route, doc)
            self._advance_progress_meta(route, doc, digest)
            return {"ok": True, "duplicate": True, "progress": {"route": route.route_id, "sequence": doc.handoff_sequence}, **local}

        event = self._event_from_progress(route, monitor, doc, submission, pr)
        target = event.target_role
        token = secrets.token_urlsafe(24) if target else None
        capsule = None
        if target:
            capsule = build_execution_capsule(
                event,
                target,
                delivery_marker(event, target),
                monitor,
                token or "",
                resolved.document,
                resolved.sha256,
            )
        mode = self.service.effective_mode()
        if mode is RelayMode.SHADOW:
            raise ProgressError("RELAY_SHADOW", "progress route cannot consume decisions while Relay is shadow")
        inserted, delivery_id = self.db.accept_event(
            event,
            event.model_dump_json(),
            new_state=transition_name(event),
            worker_role=monitor.worker_role,
            state_sha=event.candidate_sha or event.reviewed_sha or event.control_head_sha,
            target_role=target,
            body=capsule,
            delivery_token=token,
            required_apps=monitor.required_apps,
            strict_apps=monitor.strict_apps,
            awaiting_approval=mode is RelayMode.DRY_RUN,
        )
        next_dispatch = None
        if event.event_type is EventType.MENTOR_ACCEPTED:
            if doc.route_status is RouteStatus.COMPLETE:
                self.db.set_meta(self._meta(route, "current_task_id"), "")
                self.db.set_meta(self._meta(route, "current_task_file"), "")
            else:
                assert doc.next_task is not None
                next_monitor, next_resolved = self._load_task_monitor(route, doc.next_task)
                next_dispatch = self._dispatch_root(route, next_monitor, next_resolved)
        elif event.event_type is EventType.TASK_BLOCKED:
            self.service._alert(
                "warning",
                "SAT2_TASK_BLOCKED",
                submission.summary,
                monitor.task_id,
                route.pr_number,
            )

        local = self._record_local_decision(submission, event, route)
        self._advance_progress_meta(route, doc, digest)
        self.db.set_meta(self._meta(route, "pending_sequence"), "")
        self.db.set_meta(self._meta(route, "pending_sha256"), "")
        return {
            "ok": True,
            "created": inserted,
            "delivery_id": delivery_id,
            "progress": {"route": route.route_id, "sequence": doc.handoff_sequence, "status": doc.route_status.value},
            "next_dispatch": next_dispatch,
            **local,
        }
