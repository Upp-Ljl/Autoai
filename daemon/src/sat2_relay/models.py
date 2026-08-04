from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SHA_PATTERN = r"^[0-9a-f]{40}$"
SHA256_PATTERN = r"^[0-9a-f]{64}$"
KNOWN_ROLES = {"mentor", "S1", "S2", "S3", "S4", "user", "relay"}


class RelayMode(StrEnum):
    SHADOW = "shadow"
    DRY_RUN = "dry_run"
    ACTIVE = "active"
    PAUSED = "paused"


class EventType(StrEnum):
    TASK_AUTHORIZED = "SAT2_TASK_AUTHORIZED"
    WORKER_ACK = "SAT2_WORKER_ACK"
    WORKER_CHECKPOINT = "SAT2_WORKER_CHECKPOINT"
    MENTOR_CHANGES_REQUIRED = "SAT2_MENTOR_CHANGES_REQUIRED"
    MENTOR_ACCEPTED = "SAT2_MENTOR_ACCEPTED"
    TASK_BLOCKED = "SAT2_TASK_BLOCKED"
    HUMAN_GATE = "SAT2_HUMAN_GATE"
    RELAY_ALERT = "SAT2_RELAY_ALERT"
    TASK_CANCELLED = "SAT2_TASK_CANCELLED"


class DecisionName(StrEnum):
    WORKER_ACK = "WORKER_ACK"
    WORKER_CHECKPOINT = "WORKER_CHECKPOINT"
    MENTOR_CHANGES_REQUIRED = "MENTOR_CHANGES_REQUIRED"
    MENTOR_ACCEPTED = "MENTOR_ACCEPTED"
    TASK_BLOCKED = "TASK_BLOCKED"


class RelayEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = Field(default="sat2-relay/v2", pattern=r"^sat2-relay/v[12]$")
    event_id: str = Field(min_length=3, max_length=180, pattern=r"^[A-Za-z0-9._:-]+$")
    event_type: EventType
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    task_id: str = Field(min_length=2, max_length=160)
    actor_role: str = Field(min_length=2, max_length=40)
    target_role: str | None = Field(default=None, max_length=40)
    pr_number: int = Field(gt=0)
    base_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    candidate_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    reviewed_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    authorized_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    control_head_sha: str | None = Field(default=None, pattern=SHA_PATTERN)
    parent_event_id: str | None = Field(default=None, max_length=180, pattern=r"^[A-Za-z0-9._:-]+$")
    correlation_id: str | None = Field(default=None, max_length=180, pattern=r"^[A-Za-z0-9._:-]+$")
    task_spec_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    attempt: int = Field(default=1, ge=1, le=1000)
    next_actor: str | None = Field(default=None, max_length=40)
    next_task: str | None = Field(default=None, max_length=160)
    timestamp: datetime
    summary: str | None = Field(default=None, max_length=8000)
    source_comment_id: int | None = None
    source_comment_url: str | None = None
    source_actor: str | None = None

    @field_validator("actor_role", "target_role", "next_actor")
    @classmethod
    def normalize_role(cls, value: str | None) -> str | None:
        if value is None:
            return None
        lowered = value.strip().lower()
        aliases = {
            "mentor": "mentor",
            "s1": "S1",
            "s2": "S2",
            "s3": "S3",
            "s4": "S4",
            "user": "user",
            "relay": "relay",
        }
        normalized = aliases.get(lowered, value.strip())
        if normalized not in KNOWN_ROLES:
            raise ValueError(f"unknown role: {value}")
        return normalized

    @model_validator(mode="after")
    def validate_v2_causation(self) -> "RelayEvent":
        if self.protocol == "sat2-relay/v2" and self.event_type in {
            EventType.WORKER_ACK,
            EventType.WORKER_CHECKPOINT,
            EventType.MENTOR_CHANGES_REQUIRED,
            EventType.MENTOR_ACCEPTED,
        } and not self.parent_event_id:
            raise ValueError(f"{self.event_type.value} requires parent_event_id under sat2-relay/v2")
        return self


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    AWAITING_APPROVAL = "awaiting_approval"
    LEASED = "leased"
    DELIVERED = "delivered"
    RETRY = "retry"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Delivery(BaseModel):
    id: int
    event_id: str
    target_role: str
    body: str
    delivery_token: str
    required_apps: list[str] = Field(default_factory=list)
    strict_apps: bool = False
    status: DeliveryStatus
    attempt_count: int = 0
    available_at: datetime
    lease_expires_at: datetime | None = None


class DeliveryResult(BaseModel):
    success: bool
    code: str = Field(min_length=2, max_length=80)
    detail: str | None = Field(default=None, max_length=8000)
    retryable: bool | None = None
    observed_url: str | None = None
    observed_message_marker: str | None = None
    diagnostics: dict[str, Any] = Field(default_factory=dict)


class DecisionSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")

    installation_id: str = Field(min_length=8, max_length=160)
    role: str = Field(min_length=2, max_length=40)
    conversation_key: str = Field(min_length=3, max_length=240)
    delivery_id: int = Field(gt=0)
    delivery_token: str = Field(min_length=16, max_length=160)
    assistant_message_id: str = Field(min_length=1, max_length=300)
    assistant_message_hash: str = Field(pattern=SHA256_PATTERN)
    decision: DecisionName
    summary: str = Field(min_length=1, max_length=8000)
    manual: bool = False

    @field_validator("role")
    @classmethod
    def normalize_submission_role(cls, value: str) -> str:
        lowered = value.strip().lower()
        aliases = {"mentor": "mentor", "s1": "S1", "s2": "S2", "s3": "S3", "s4": "S4"}
        normalized = aliases.get(lowered, value.strip())
        if normalized not in {"mentor", "S1", "S2", "S3", "S4"}:
            raise ValueError(f"unsupported endpoint role: {value}")
        return normalized


class HumanConfirmation(BaseModel):
    confirm: bool = True


class TaskAuthorizationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(default="Task authorized from the local SAT2 Relay Control Center.", min_length=1, max_length=8000)
    confirm: bool = False


class OutboxStatus(StrEnum):
    PENDING = "pending"
    WAITING_FOR_HUMAN = "waiting_for_human"
    PUBLISHING = "publishing"
    PUBLISH_UNCERTAIN = "publish_uncertain"
    PUBLISHED = "published"
    RETRY = "retry"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class BindingHeartbeat(BaseModel):
    url: str
    conversation_key: str | None = None
    tab_id: int | None = None
    composer_ready: bool = False
    send_ready: bool = False
    busy: bool = False
    login_required: bool = False
    confirmation_visible: bool = False
    error: str | None = None
    checked_at: str | None = None


class Heartbeat(BaseModel):
    installation_id: str = Field(min_length=8, max_length=160)
    extension_version: str = Field(min_length=1, max_length=40)
    auto_enabled: bool
    bindings: dict[str, str | dict[str, Any]] = Field(default_factory=dict)
    active_roles: list[str] = Field(default_factory=list)
    browser: str | None = None
    last_cycle_at: str | None = None
    last_cycle_result: str | None = None


class RepoMonitor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pr_number: int = Field(gt=0)
    task_id: str
    worker_role: str
    enabled: bool = True
    required_apps: list[str] = Field(default_factory=lambda: ["GitHub"])
    strict_apps: bool = False
    start_after_comment_id: int | None = Field(default=None, ge=1)
    task_file: str | None = None
    task_ref: str = "@config"
    task_sha256: str | None = Field(default=None, pattern=SHA256_PATTERN)
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

    @field_validator("worker_role")
    @classmethod
    def validate_worker_role(cls, value: str) -> str:
        normalized = value.strip().upper()
        if normalized not in {"S1", "S2", "S3", "S4"}:
            raise ValueError("worker_role must be S1, S2, S3, or S4")
        return normalized

    @field_validator("task_ref")
    @classmethod
    def validate_task_ref(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return "@config"
        if value.startswith("@") and value not in {"@config", "@default", "@pr-head", "@pr-base"}:
            raise ValueError("task_ref special value must be @config, @default, @pr-head, or @pr-base")
        return value


class RepoRelayConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol_version: str = Field(default="sat2-relay/v2", pattern=r"^sat2-relay/v[12]$")
    enabled: bool = False
    mode: RelayMode = RelayMode.SHADOW
    repository: str
    integration_branch: str | None = None
    trusted_actors: list[str] = Field(default_factory=list)
    alert_issue: int | None = None
    alert_mention: str | None = None
    poll_interval_seconds: int = Field(default=15, ge=5, le=3600)
    error_retry_seconds: int = Field(default=10, ge=3, le=300)
    delivery_lease_seconds: int = Field(default=90, ge=30, le=1800)
    maximum_delivery_attempts: int = Field(default=8, ge=1, le=20)
    retry_delays_seconds: list[int] = Field(default_factory=lambda: [5, 10, 20, 30], min_length=1, max_length=20)
    extension_stale_seconds: int = Field(default=90, ge=30, le=86400)
    process_existing_events_on_first_poll: bool = False
    monitors: list[RepoMonitor] = Field(default_factory=list)
    human_gates: list[str] = Field(default_factory=lambda: [
        "merge",
        "mark_ready_for_review",
        "workflow_dispatch",
        "local_qualification",
        "formal_experiment",
        "registry_change",
        "seed_change",
        "accepted_evidence_change",
        "paper_claim_change",
        "paper_number_change",
        "force_push",
        "base_branch_change",
        "scope_expansion",
        "cross_worker_path_conflict",
    ])

    @field_validator("retry_delays_seconds")
    @classmethod
    def validate_retry_delays(cls, value: list[int]) -> list[int]:
        if any(delay < 1 or delay > 300 for delay in value):
            raise ValueError("retry delays must be between 1 and 300 seconds")
        if value != sorted(value):
            raise ValueError("retry delays must be non-decreasing")
        return value

    @model_validator(mode="after")
    def validate_control_plane(self) -> "RepoRelayConfig":
        monitor_keys: set[tuple[int, str]] = set()
        task_ids: set[str] = set()
        for monitor in self.monitors:
            key = (monitor.pr_number, monitor.task_id)
            if key in monitor_keys or monitor.task_id in task_ids:
                raise ValueError(f"duplicate Relay monitor for task {monitor.task_id}")
            monitor_keys.add(key)
            task_ids.add(monitor.task_id)
            if monitor.enabled and not monitor.task_file:
                raise ValueError(f"enabled monitor {monitor.task_id} requires task_file")
            if monitor.enabled and not monitor.allowed_paths:
                raise ValueError(f"enabled monitor {monitor.task_id} requires allowed_paths")
        if self.enabled and not self.trusted_actors:
            raise ValueError("enabled Relay requires at least one trusted actor")
        return self
