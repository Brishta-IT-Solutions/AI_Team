"""Append-only audit (FR-27, NFR-07). Allowed and denied sensitive actions are both recorded."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from control_api.db.models import AuditEvent
from control_api.db.session import get_sessionmaker
from control_api.domain.approvals import canonical_hash
from control_api.domain.permissions import Principal
from control_api.services.redaction import redact


def state_hash(state: dict[str, Any] | None) -> str | None:
    return None if state is None else canonical_hash(state)


def audit(
    session: Session,
    principal: Principal,
    *,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID | None,
    project_id: uuid.UUID | str | None,
    correlation_id: str,
    outcome: str = "ALLOWED",
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    policy_version: int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        actor_kind=str(principal.kind),
        actor_id=principal.id,
        project_id=uuid.UUID(str(project_id)) if project_id else None,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id else None,
        outcome=outcome,
        reason=redact(reason) if reason else None,
        before_hash=state_hash(before),
        after_hash=state_hash(after),
        policy_version=policy_version,
        correlation_id=correlation_id,
        details=redact(details or {}),
    )
    session.add(event)
    return event


def record_denial(
    principal: Principal,
    *,
    action: str,
    object_type: str,
    object_id: str | uuid.UUID | None,
    project_id: uuid.UUID | str | None,
    correlation_id: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Persist a denial in its own transaction so it survives the request's rollback."""
    with get_sessionmaker()() as session, session.begin():
        audit(
            session,
            principal,
            action=action,
            object_type=object_type,
            object_id=object_id,
            project_id=project_id,
            correlation_id=correlation_id,
            outcome="DENIED",
            reason=reason,
            details=details,
        )
