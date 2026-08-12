from __future__ import annotations

import hashlib
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
    def validate_shape(self) -> "ProgressDocument":
        if self.handoff_sequence == 0:
            if self.event_type is not ProgressEventType.ROUTE_INIT:
                raise ValueError("sequence 0 must use ROUTE_INIT")
            if self.parent_sequence is not None:
                raise ValueError("ROUTE_INIT must use parent_sequence: null")
            if self.route_status is not RouteStatus.ACTIVE:
                raise ValueError("ROUTE_INIT requires route_status: ACTIVE")
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
            elif self.route_status is not RouteStatus.ACTIVE:
                raise ValueError("MENTOR_ACCEPTED requires route_status ACTIVE or COMPLETE")
            elif not self.next_task:
                raise ValueError("MENTOR_ACCEPTED requires next_task unless route_status is COMPLETE")
        elif self.event_type is ProgressEventType.TASK_BLOCKED:
            if self.route_status is not RouteStatus.BLOCKED:
                raise ValueError("TASK_BLOCKED requires route_status: BLOCKED")
        elif self.route_status is not RouteStatus.ACTIVE:
            raise ValueError(f"{self.event_type.value} requires route_status: ACTIVE")
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


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in value)[:100]


class ParallelAutonomyController:
    """Deterministic route-local control plane for progress-driven SAT2 work."""

    def __init__(self, service: Any, db: Any):
        self.service = service
        self.db = db

    @property
    def config(self):
        if not self.service.repo_config:
            self.service.refresh_config()
        return self.service.repo_config

    @staticmethod
    def _meta(route: RepoRoute, key: str) -> str:
        return f"route:{route.route_id}:{key}"

    @staticmethod
    def _runtime_key(task_id: str) -> str:
        return f"runtime_monitor:{task_id}"

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

    def _resolve_ref(self, value: str | None, fallback: str) -> str | None:
        value = (value or fallback).strip()
        if value == "@config":
            return self.service.local.repository_config_ref
        if value == "@default":
            return None
        if value in {"@pr-head", "@pr-base"}:
            raise ProgressError("ROUTE_REF_INVALID", f"route ref {value} is not stable enough for autonomy")
        return value

    def progress_ref(self, route: RepoRoute) -> str | None:
        return self._resolve_ref(route.progress_ref, self.service.local.repository_config_ref)

    def task_ref(self, route: RepoRoute) -> str | None:
        return self._resolve_ref(route.task_ref, route.progress_ref)

    def _load_progress(self, route: RepoRoute) -> tuple[ProgressDocument, str]:
        ref = self.progress_ref(route)
        try:
            text = self.service.github.get_content_text(self.config.repository, route.progress_file, ref)
            raw = yaml.safe_load(text)
            if not isinstance(raw, dict):
                raise ValueError("document is not a mapping")
            doc = ProgressDocument.model_validate(raw)
        except ProgressError:
            raise
        except Exception as exc:
            raise ProgressError("PROGRESS_INVALID", f"{route.route_id}: {exc}") from exc
        if doc.route != route.route_id:
            raise ProgressError("PROGRESS_ROUTE_MISMATCH", f"progress route {doc.route} != {route.route_id}")
        return doc, hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _inside(root: str, path: str) -> bool:
        root_n = PurePosixPath(root).as_posix().rstrip("/")
        path_n = PurePosixPath(path).as_posix().lstrip("/")
        return path_n == root_n or path_n.startswith(root_n + "/")

    def _load_task_monitor(self, route: RepoRoute, task_file: str) -> tuple[RepoMonitor, Any]:
        if not self._inside(route.task_root, task_file):
            raise ProgressError("NEXT_TASK_OUTSIDE_ROUTE", f"{task_file} is outside {route.task_root}")
        ref = self.task_ref(route)
        try:
            text = self.service.github.get_content_text(self.config.repository, task_file, ref)
            doc = yaml.safe_load(text)
        except Exception as exc:
            raise ProgressError("NEXT_TASK_UNAVAILABLE", f"cannot load {task_file}@{ref}: {exc}") from exc
        if not isinstance(doc, dict):
            raise ProgressError("NEXT_TASK_INVALID", f"task {task_file} is not a YAML mapping")
        task_id = str(doc.get("task_id") or "").strip()
        if not task_id:
            raise ProgressError("NEXT_TASK_INVALID", f"task {task_file} has no task_id")
        if normalize_session_role(str(doc.get("worker_role") or route.worker_role)) != route.worker_role:
            raise ProgressError("NEXT_TASK_ROLE_MISMATCH", f"task {task_id} worker differs from route")
        if normalize_session_role(str(doc.get("mentor_role") or route.mentor_role)) != route.mentor_role:
            raise ProgressError("NEXT_TASK_ROLE_MISMATCH", f"task {task_id} mentor differs from route")
        if doc.get("route_id") and str(doc["route_id"]) != route.route_id:
            raise ProgressError("NEXT_TASK_ROUTE_MISMATCH", f"task {task_id} route differs from {route.route_id}")
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
            allowed_paths=[str(x) for x in (doc.get("allowed_paths") or [])],
            forbidden_paths=[str(x) for x in (doc.get("forbidden_paths") or [])],
            dependencies=[str(x) for x in (doc.get("dependencies") or [])],
        )
        pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
        if pr.get("state") != "open":
            raise ProgressError("PR_NOT_OPEN", f"Route PR #{route.pr_number} is {pr.get('state')}")
        resolved = self.service.load_task_spec(monitor, pr)
        self.service.validate_task_spec_ready(monitor, pr, resolved)
        return monitor, resolved

    def _event_id(self, route: RepoRoute, doc: ProgressDocument) -> str:
        return f"progress.{_safe(route.route_id)}.{doc.handoff_sequence}.{_safe(doc.task_id or 'init')}"

    def _root_id(self, route: RepoRoute, monitor: RepoMonitor, contract: str, head: str) -> str:
        digest = hashlib.sha256(
            "\n".join([route.route_id, monitor.task_id, contract, head, "PROGRESS_DOCUMENT_DISPATCH"]).encode()
        ).hexdigest()
        return f"{_safe(route.route_id)}.{_safe(monitor.task_id)}.root.{digest[:24]}"

    def _save_monitor(self, route: RepoRoute, monitor: RepoMonitor) -> None:
        self.db.set_meta(self._runtime_key(monitor.task_id), monitor.model_dump_json())
        self.db.set_meta(self._meta(route, "current_task_id"), monitor.task_id)
        self.db.set_meta(self._meta(route, "current_task_file"), str(monitor.task_file or ""))
        self.db.set_meta(self._meta(route, "status"), RouteStatus.ACTIVE.value)

    def _dispatch_root(self, route: RepoRoute, monitor: RepoMonitor, resolved: Any | None = None) -> dict[str, Any]:
        pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
        if pr.get("state") != "open":
            raise ProgressError("PR_NOT_OPEN", f"Route PR #{route.pr_number} is {pr.get('state')}")
        resolved = resolved or self.service.load_task_spec(monitor, pr)
        self.service.validate_task_spec_ready(monitor, pr, resolved)
        self.service._validate_dependencies(monitor)
        state = self.db.task_state(monitor.task_id)
        if state and str(state["state"]) not in {"READY", "DORMANT"}:
            self._save_monitor(route, monitor)
            return {"created": False, "task_id": monitor.task_id, "state": str(state["state"])}
        head = str((pr.get("head") or {}).get("sha") or "")
        base = str((pr.get("base") or {}).get("sha") or "")
        event_id = self._root_id(route, monitor, resolved.sha256, head)
        event = RelayEvent.model_validate({
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
            "summary": f"Route {route.route_id} deterministic document dispatch",
            "source_comment_id": 0,
            "source_actor": "route-config",
        })
        validate_actor_semantics(event, monitor)
        commits = {str(x.get("sha") or "") for x in self.service.github.list_pull_request_commits(self.config.repository, route.pr_number)}
        validate_pr_binding(event, pr, commits)
        validate_transition(event, state["state"] if state else None, state["last_event_id"] if state else None)
        if self.service.effective_mode() is RelayMode.SHADOW:
            return {"created": False, "task_id": monitor.task_id, "shadow": True}
        token = secrets.token_urlsafe(24)
        body = build_execution_capsule(event, route.worker_role, delivery_marker(event, route.worker_role), monitor, token, resolved.document, resolved.sha256)
        inserted, delivery_id = self.db.accept_event(
            event,
            event.model_dump_json(),
            new_state=transition_name(event),
            worker_role=monitor.worker_role,
            state_sha=head,
            target_role=route.worker_role,
            body=body,
            delivery_token=token,
            required_apps=monitor.required_apps,
            strict_apps=monitor.strict_apps,
            awaiting_approval=self.service.effective_mode() is RelayMode.DRY_RUN,
        )
        self._save_monitor(route, monitor)
        self.db.set_meta(f"task_contract:{monitor.task_id}:sha256", resolved.sha256)
        self.db.set_meta(f"task_contract:{monitor.task_id}:path", str(monitor.task_file or ""))
        self.db.set_meta(f"task_contract:{monitor.task_id}:ref", str(monitor.task_ref))
        return {"created": inserted, "task_id": monitor.task_id, "delivery_id": delivery_id}

    def _processed(self, route: RepoRoute) -> int | None:
        raw = self.db.get_meta(self._meta(route, "processed_sequence"))
        return int(raw) if raw is not None else None

    def _advance(self, route: RepoRoute, doc: ProgressDocument, digest: str) -> None:
        self.db.set_meta(self._meta(route, "processed_sequence"), str(doc.handoff_sequence))
        self.db.set_meta(self._meta(route, "stage"), str(doc.stage))
        self.db.set_meta(self._meta(route, "progress_sha256"), digest)
        self.db.set_meta(self._meta(route, "status"), doc.route_status.value)

    def _bootstrap(self, route: RepoRoute) -> dict[str, Any] | None:
        if route.signal_mode is not RouteSignalMode.PROGRESS or not route.bootstrap_task_file:
            return None
        if self.db.get_meta(self._meta(route, "status")) in {RouteStatus.BLOCKED.value, RouteStatus.COMPLETE.value}:
            return None
        if self.db.get_meta(self._meta(route, "current_task_id")):
            return None
        monitor, resolved = self._load_task_monitor(route, route.bootstrap_task_file)
        return self._dispatch_root(route, monitor, resolved)

    def _recover_next(self, route: RepoRoute, doc: ProgressDocument) -> None:
        if doc.event_type is not ProgressEventType.MENTOR_ACCEPTED or doc.route_status is RouteStatus.COMPLETE:
            return
        if not doc.next_task:
            raise ProgressError("NEXT_TASK_MISSING", "accepted handoff has no next_task")
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
        processed = self._processed(route)
        if processed is None:
            if doc.handoff_sequence != 0 or doc.event_type is not ProgressEventType.ROUTE_INIT:
                raise ProgressError("PROGRESS_BASELINE_REQUIRED", f"route {route.route_id} must begin with sequence 0 ROUTE_INIT")
            if doc.updated_by not in {route.mentor_role, route.worker_role}:
                raise ProgressError("PROGRESS_ROLE_INVALID", f"ROUTE_INIT actor {doc.updated_by} is outside route")
            self._advance(route, doc, digest)
            processed = 0
        elif doc.handoff_sequence < processed:
            raise ProgressError("PROGRESS_SEQUENCE_ROLLBACK", f"route {route.route_id}: {processed} -> {doc.handoff_sequence}")
        elif doc.handoff_sequence > processed + 1:
            raise ProgressError("PROGRESS_SEQUENCE_GAP", f"route {route.route_id}: {processed} -> {doc.handoff_sequence}")
        elif doc.handoff_sequence == processed + 1:
            event = self.db.event_payload(self._event_id(route, doc))
            if event:
                # The event contains the accepted next-task path; require the current
                # progress document to agree before completing crash recovery.
                if str(event.get("next_task") or "") != str(doc.next_task or ""):
                    raise ProgressError("PROGRESS_RECOVERY_MISMATCH", "current progress differs from the committed event")
                self._recover_next(route, doc)
                self._advance(route, doc, digest)
            else:
                self.db.set_meta(self._meta(route, "pending_sequence"), str(doc.handoff_sequence))
                self.db.set_meta(self._meta(route, "pending_sha256"), digest)
        self._bootstrap(route)
        return {"route": route.route_id, "sequence": doc.handoff_sequence, "processed": self._processed(route)}

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
            except ProgressError as exc:
                counts["route_errors"] += 1
                self.service._alert("error", exc.code, exc.detail, self.db.get_meta(self._meta(route, "current_task_id")), route.pr_number, 60)
            except Exception as exc:
                counts["route_errors"] += 1
                self.service._alert("error", "PROGRESS_ROUTE_FAILED", f"{route.route_id}: {exc}", self.db.get_meta(self._meta(route, "current_task_id")), route.pr_number, 60)
        return counts

    def _validate_stage(self, route: RepoRoute, doc: ProgressDocument) -> None:
        raw = self.db.get_meta(self._meta(route, "stage"))
        if raw is None:
            return
        prev = int(raw)
        if doc.event_type is ProgressEventType.MENTOR_ACCEPTED and doc.route_status is not RouteStatus.COMPLETE:
            if doc.stage != prev + 1:
                raise ProgressError("PROGRESS_STAGE_INVALID", f"expected stage {prev + 1}, got {doc.stage}")
        elif doc.stage != prev:
            raise ProgressError("PROGRESS_STAGE_INVALID", f"expected stage {prev}, got {doc.stage}")

    def _validate_attestation(self, route: RepoRoute, monitor: RepoMonitor, submission: DecisionSubmission) -> tuple[ProgressDocument, str, dict[str, Any], Any]:
        if submission.decision is DecisionName.WORKER_ACK:
            raise ProgressError("PROGRESS_ACK_NOT_USED", "WORKER_ACK does not advance progress")
        expected = _DECISION_PROGRESS.get(submission.decision)
        if expected is None:
            raise ProgressError("PROGRESS_DECISION_INVALID", str(submission.decision))
        doc, digest = self._load_progress(route)
        processed = self._processed(route)
        if processed is None:
            raise ProgressError("PROGRESS_BASELINE_REQUIRED", "route is not baselined")
        if doc.handoff_sequence != processed + 1:
            if doc.handoff_sequence == processed and self.db.event_payload(self._event_id(route, doc)) and doc.event_type is expected:
                pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
                return doc, digest, pr, self.service.load_task_spec(monitor, pr)
            code = "PROGRESS_SEQUENCE_GAP" if doc.handoff_sequence > processed + 1 else "PROGRESS_SEQUENCE_ROLLBACK"
            raise ProgressError(code, f"expected {processed + 1}, got {doc.handoff_sequence}")
        if doc.parent_sequence != processed or doc.event_type is not expected:
            raise ProgressError("PROGRESS_DECISION_MISMATCH", "progress sequence/event does not match Session decision")
        if doc.updated_by != submission.role:
            raise ProgressError("PROGRESS_IDENTITY_MISMATCH", f"Session {submission.role} != progress {doc.updated_by}")
        if doc.task_id != monitor.task_id or doc.current_task != monitor.task_file or doc.pr_number != route.pr_number:
            raise ProgressError("PROGRESS_TASK_MISMATCH", "progress task/PR differs from active delivery")
        self._validate_stage(route, doc)
        pr = self.service.github.get_pull_request(self.config.repository, route.pr_number)
        if pr.get("state") != "open":
            raise ProgressError("PR_NOT_OPEN", f"Route PR #{route.pr_number} is {pr.get('state')}")
        head = str((pr.get("head") or {}).get("sha") or "")
        if doc.control_head_sha != head:
            raise ProgressError("STALE_PR_HEAD", f"progress head {doc.control_head_sha} != PR head {head}")
        resolved = self.service.load_task_spec(monitor, pr)
        frozen = self.db.get_meta(f"task_contract:{monitor.task_id}:sha256")
        if not frozen or frozen != resolved.sha256 or doc.task_contract_sha256 != frozen:
            raise ProgressError("TASK_CONTRACT_MISMATCH", f"task contract mismatch for {monitor.task_id}")
        if doc.event_type is ProgressEventType.WORKER_CHECKPOINT:
            if submission.role != route.worker_role or doc.candidate_sha != head:
                raise ProgressError("STALE_PR_HEAD", "worker checkpoint role/SHA mismatch")
        elif doc.event_type in {ProgressEventType.MENTOR_CHANGES_REQUIRED, ProgressEventType.MENTOR_ACCEPTED}:
            state = self.db.task_state(monitor.task_id)
            checkpoint = str(state["sha"] or "") if state else ""
            if submission.role != route.mentor_role or not state or str(state["state"]) != "MENTOR_REVIEW":
                raise ProgressError("ILLEGAL_STATE_TRANSITION", "Mentor decision requires MENTOR_REVIEW")
            if doc.reviewed_sha != checkpoint or doc.reviewed_sha != head:
                raise ProgressError("STALE_PR_HEAD", f"reviewed {doc.reviewed_sha} != checkpoint/current {checkpoint}/{head}")
        return doc, digest, pr, resolved

    def _event(self, route: RepoRoute, monitor: RepoMonitor, doc: ProgressDocument, submission: DecisionSubmission, pr: dict[str, Any]) -> RelayEvent:
        event_type = _PROGRESS_EVENT[doc.event_type]
        state = self.db.task_state(monitor.task_id)
        parent = str(state["last_event_id"] or "") if state else None
        parent_payload = self.db.event_payload(parent) if parent else None
        head = str((pr.get("head") or {}).get("sha") or "")
        fields: dict[str, Any] = {
            "protocol": "sat2-relay/v2",
            "event_id": self._event_id(route, doc),
            "event_type": event_type.value,
            "repository": self.config.repository,
            "task_id": monitor.task_id,
            "actor_role": submission.role,
            "target_role": resolve_target(RelayEvent.model_construct(event_type=event_type, actor_role=submission.role), monitor),
            "pr_number": route.pr_number,
            "parent_event_id": parent,
            "correlation_id": str((parent_payload or {}).get("correlation_id") or (parent_payload or {}).get("event_id") or parent),
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
        commits = {str(x.get("sha") or "") for x in self.service.github.list_pull_request_commits(self.config.repository, route.pr_number)}
        validate_pr_binding(event, pr, commits)
        validate_transition(event, state["state"] if state else None, state["last_event_id"] if state else None)
        changed = [str(x.get("filename") or "") for x in self.service.github.list_pull_request_files(self.config.repository, route.pr_number)]
        self.service._validate_monitor_contract(monitor, pr, changed, event)
        return event

    def _audit(self, route: RepoRoute, submission: DecisionSubmission, event: RelayEvent) -> dict[str, Any]:
        key = hashlib.sha256("\n".join([route.route_id, event.event_id, str(submission.delivery_id), submission.assistant_message_hash, submission.decision.value]).encode()).hexdigest()
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

    def submit_decision(self, submission: DecisionSubmission, context: dict[str, Any], monitor: RepoMonitor) -> dict[str, Any]:
        route = self.route_for_monitor(monitor)
        if not route or route.signal_mode is not RouteSignalMode.PROGRESS:
            raise ProgressError("ROUTE_NOT_PROGRESS_ACTIVE", f"task {monitor.task_id} is not progress-active")
        doc, digest, pr, resolved = self._validate_attestation(route, monitor, submission)
        event_id = self._event_id(route, doc)
        existing = self.db.event_payload(event_id)
        if existing:
            event = RelayEvent.model_validate(existing)
            audit = self._audit(route, submission, event)
            self._recover_next(route, doc)
            self._advance(route, doc, digest)
            return {"ok": True, "duplicate": True, "progress": {"route": route.route_id, "sequence": doc.handoff_sequence}, **audit}
        event = self._event(route, monitor, doc, submission, pr)
        target = event.target_role
        token = secrets.token_urlsafe(24) if target else None
        body = build_execution_capsule(event, target, delivery_marker(event, target), monitor, token or "", resolved.document, resolved.sha256) if target else None
        if self.service.effective_mode() is RelayMode.SHADOW:
            raise ProgressError("RELAY_SHADOW", "progress route cannot consume decisions while Relay is shadow")
        inserted, delivery_id = self.db.accept_event(
            event,
            event.model_dump_json(),
            new_state=transition_name(event),
            worker_role=monitor.worker_role,
            state_sha=event.candidate_sha or event.reviewed_sha or event.control_head_sha,
            target_role=target,
            body=body,
            delivery_token=token,
            required_apps=monitor.required_apps,
            strict_apps=monitor.strict_apps,
            awaiting_approval=self.service.effective_mode() is RelayMode.DRY_RUN,
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
            self.service._alert("warning", "SAT2_TASK_BLOCKED", submission.summary, monitor.task_id, route.pr_number)
        audit = self._audit(route, submission, event)
        self._advance(route, doc, digest)
        self.db.set_meta(self._meta(route, "pending_sequence"), "")
        self.db.set_meta(self._meta(route, "pending_sha256"), "")
        return {
            "ok": True,
            "created": inserted,
            "delivery_id": delivery_id,
            "progress": {"route": route.route_id, "sequence": doc.handoff_sequence, "status": doc.route_status.value},
            "next_dispatch": next_dispatch,
            **audit,
        }
