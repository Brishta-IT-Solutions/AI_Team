"""Logical schema (FRD FR-22). PostgreSQL is the authority for workflow state.

UUID keys, UTC timestamps, project_id on project-owned rows, JSONB payloads with
versioned validation, Numeric for money. Approvals and audit are append-only and
protected by database triggers (see migrations).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


# Global, strictly increasing fencing tokens for branch leases.
lease_fencing_seq = Sequence("lease_fencing_seq", metadata=Base.metadata)


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


def _project_fk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id"), nullable=False, index=True
    )


# ---------------------------------------------------------------- identity


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    subject: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    workspace_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _ts()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "project_id"),)
    id: Mapped[uuid.UUID] = _pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    project_id: Mapped[uuid.UUID] = _project_fk()
    roles: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- projects


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("status IN ('DRAFT','ACTIVE','DISABLED','ARCHIVED')", name="status"),
        CheckConstraint("key ~ '^[A-Z][A-Z0-9]{1,9}$'", name="key_format"),
    )
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    classification: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    policy: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    task_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = _ts()


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id"), unique=True, nullable=False
    )
    github_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    installation_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    owner: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    base_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    # Facts from the GitHub inspector; unknown until verified (never trusted from users).
    verification: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class AgentConfig(Base):
    """Immutable configuration versions; runs reference the version they froze."""

    __tablename__ = "agent_configs"
    __table_args__ = (UniqueConstraint("project_id", "role", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False)
    endpoint_ref: Mapped[str | None] = mapped_column(String(500))
    auth_method: Mapped[str] = mapped_column(String(40), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    permissions: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    secret_ref: Mapped[str | None] = mapped_column(String(500))  # vault reference only
    output_schema_version: Mapped[str] = mapped_column(String(16), nullable=False)
    connection_test: Mapped[dict | None] = mapped_column(JSONB)
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- tasks


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "key"),
        CheckConstraint("priority IN ('P0','P1','P2','P3')", name="priority"),
        CheckConstraint("repair_count >= 0 AND repair_limit >= 0", name="repairs"),
    )
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    key: Mapped[str] = mapped_column(String(24), nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    priority: Mapped[str] = mapped_column(String(2), nullable=False)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default="NEW")
    execution_status: Mapped[str] = mapped_column(String(16), nullable=False, default="IDLE")
    status_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    current_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirement_versions.id", use_alter=True, name="fk_task_current_spec")
    )
    approved_spec_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("requirement_versions.id", use_alter=True, name="fk_task_approved_spec")
    )
    head_sha: Mapped[str | None] = mapped_column(String(40))
    base_sha: Mapped[str | None] = mapped_column(String(40))
    current_qa_report_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    branch: Mapped[str | None] = mapped_column(String(255))
    repair_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repair_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (CheckConstraint("task_id <> depends_on_id", name="no_self_dependency"),)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    depends_on_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), primary_key=True)


class RequirementVersion(Base):
    """Immutable once written. Edits create a new version (PRD §3)."""

    __tablename__ = "requirement_versions"
    __table_args__ = (UniqueConstraint("task_id", "version"),)
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_by_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = _ts()


class AcceptanceCriterion(Base):
    __tablename__ = "acceptance_criteria"
    __table_args__ = (UniqueConstraint("spec_id", "stable_key"),)
    id: Mapped[uuid.UUID] = _pk()
    spec_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("requirement_versions.id"), nullable=False, index=True
    )
    stable_key: Mapped[str] = mapped_column(String(24), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    verification: Mapped[str] = mapped_column(Text, nullable=False)
    mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False)


# ---------------------------------------------------------------- approvals and audit


class Approval(Base):
    """Append-only. Revocation/supersession is a new row, never an edit."""

    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint("gate IN ('REQUIREMENTS','MERGE','RELEASE')", name="gate"),
        CheckConstraint(
            "decision IN ('APPROVED','CHANGES_REQUESTED','REJECTED','REVOKED')", name="decision"
        ),
        CheckConstraint("(task_id IS NULL) <> (release_id IS NULL)", name="one_subject"),
    )
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    release_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    gate: Mapped[str] = mapped_column(String(16), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    scope_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # NULL only for system-recorded REVOKED rows; human decisions always carry a human.
    human_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("approvals.id"))
    created_at: Mapped[datetime] = _ts()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_project_time", "project_id", "created_at"),)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4
    )
    actor_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    object_type: Mapped[str] = mapped_column(String(40), nullable=False)
    object_id: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str] = mapped_column(String(10), nullable=False)  # ALLOWED | DENIED
    reason: Mapped[str | None] = mapped_column(Text)
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _ts()


class OutboxEvent(Base):
    """Transactional outbox. `seq` doubles as the client event cursor (FR-23)."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_undelivered", "seq", postgresql_where=text("delivered_at IS NULL")),
        Index("ix_outbox_project_seq", "project_id", "seq"),
    )
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=False, default=uuid.uuid4
    )
    project_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(40), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_version: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ProcessedEvent(Base):
    """Consumer-side deduplication: at-least-once delivery, at-most-once effect."""

    __tablename__ = "processed_events"
    consumer: Mapped[str] = mapped_column(String(80), primary_key=True)
    event_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = _ts()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    principal_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- execution control


class BranchLease(Base):
    """One active writer per branch, fenced by a monotonically increasing token (FR-02)."""

    __tablename__ = "branch_leases"
    __table_args__ = (
        Index(
            "ux_branch_active_lease",
            "repository_id",
            "branch",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    repository_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repositories.id"), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    branch: Mapped[str] = mapped_column(String(255), nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    holder: Mapped[str] = mapped_column(String(255), nullable=False)
    heartbeat_at: Mapped[datetime] = _ts()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(String(40))


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("project_id", "scope", "period"),
        CheckConstraint("scope IN ('PROJECT_MONTH','TICKET','RUN')", name="scope"),
    )
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    period: Mapped[str] = mapped_column(String(16), nullable=False)  # e.g. 2026-09 or "*"
    cap: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")


# ---------------------------------------------------------------- execution (vertical slice)


class Run(Base):
    """One execution of an agent role for a ticket (FR-02, FR-12, FR-20).

    The envelope freezes everything the run was given. A worker claims a queued run,
    heartbeats while it works, and submits one result. Child runs (junior work) hang
    off a parent developer run.
    """

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint("role IN ('BA','DEVELOPER','QA','JUNIOR')", name="role"),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','BLOCKED','CANCELLED')", name="status"
        ),
        # One active parent execution per ticket (FR-02).
        Index("ux_one_active_parent_run", "task_id", unique=True,
              postgresql_where=text("parent_run_id IS NULL AND status IN ('QUEUED','RUNNING')")),
        Index("ix_runs_queue", "project_id", "role", "created_at",
              postgresql_where=text("status = 'QUEUED'")),
    )
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    parent_run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("runs.id"))
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="QUEUED")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    envelope: Mapped[dict] = mapped_column(JSONB, nullable=False)
    worker_id: Mapped[str | None] = mapped_column(String(120))
    claim_token: Mapped[str | None] = mapped_column(String(64))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    milestone: Mapped[str | None] = mapped_column(String(200))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(200))
    result: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    usage: Mapped[dict | None] = mapped_column(JSONB)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    cost_quality: Mapped[str] = mapped_column(String(24), nullable=False, default="UNKNOWN")
    created_at: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEvent(Base):
    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "sequence"),)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")  # sanitized
    created_at: Mapped[datetime] = _ts()


class QAReportRecord(Base):
    """Immutable QA reports; a retest produces a new report (FR-18)."""

    __tablename__ = "qa_reports"
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), nullable=False)
    spec_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("requirement_versions.id"), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    base_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    verdict: Mapped[str] = mapped_column(String(10), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _ts()


class Defect(Base):
    """Keyed by ticket + normalized failure signature so retests update, not duplicate."""

    __tablename__ = "defects"
    __table_args__ = (UniqueConstraint("task_id", "signature"),)
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    signature: Mapped[str] = mapped_column(String(64), nullable=False)
    ac_id: Mapped[str | None] = mapped_column(String(24))
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="OPEN")
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False)
    first_report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("qa_reports.id"), nullable=False)
    last_report_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("qa_reports.id"), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class Reservation(Base):
    """Budget held before dispatch (FR-25); released with the run's measured cost."""

    __tablename__ = "reservations"
    __table_args__ = (CheckConstraint("status IN ('ACTIVE','RELEASED')", name="status"),)
    id: Mapped[uuid.UUID] = _pk()
    project_id: Mapped[uuid.UUID] = _project_fk()
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False, index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"), unique=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = _ts()


class Worker(Base):
    """Last known health of each worker process and the team members it can run."""

    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    project_id: Mapped[uuid.UUID] = _project_fk()
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[str | None] = mapped_column(String(40))
    last_seen_at: Mapped[datetime] = _ts()
