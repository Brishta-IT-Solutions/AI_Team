"""Branch write leases with fencing tokens (FR-02, FR-24, AT-05, AT-14).

One active writer per branch. Tokens come from a database sequence, so they are
strictly increasing across all leases; the Git broker and result API reject any
token that is not the branch's current active token. A stale worker can neither
push nor finalize results.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from control_api.db.models import BranchLease, utcnow
from control_api.errors import Conflict

HEARTBEAT_INTERVAL_SECONDS = 15
DEFAULT_TTL_SECONDS = 60


@dataclass(frozen=True)
class LeaseGrant:
    lease_id: uuid.UUID
    fencing_token: int
    branch: str


def _expire_stale(session: Session, repository_id: uuid.UUID, branch: str) -> None:
    now = utcnow()
    stale = session.scalars(
        select(BranchLease)
        .where(
            BranchLease.repository_id == repository_id,
            BranchLease.branch == branch,
            BranchLease.released_at.is_(None),
            BranchLease.expires_at <= now,
        )
        .with_for_update()
    ).all()
    for lease in stale:
        lease.released_at = now
        lease.release_reason = "EXPIRED"
    session.flush()


def acquire(
    session: Session,
    *,
    project_id: uuid.UUID,
    repository_id: uuid.UUID,
    task_id: uuid.UUID,
    branch: str,
    holder: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> LeaseGrant:
    _expire_stale(session, repository_id, branch)
    active = session.scalar(
        select(BranchLease).where(
            BranchLease.repository_id == repository_id,
            BranchLease.branch == branch,
            BranchLease.released_at.is_(None),
        )
    )
    if active is not None:
        if active.holder == holder and active.task_id == task_id:
            return LeaseGrant(active.id, active.fencing_token, branch)
        raise Conflict(f"branch {branch!r} already has an active writer", code="lease_held")
    token = session.execute(text("SELECT nextval('lease_fencing_seq')")).scalar_one()
    lease = BranchLease(
        project_id=project_id,
        repository_id=repository_id,
        task_id=task_id,
        branch=branch,
        fencing_token=token,
        holder=holder,
        expires_at=utcnow() + timedelta(seconds=ttl_seconds),
    )
    session.add(lease)
    session.flush()  # the partial unique index arbitrates concurrent acquirers
    return LeaseGrant(lease.id, token, branch)


def heartbeat(
    session: Session, *, branch: str, repository_id: uuid.UUID, token: int,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> None:
    lease = _active_for_token(session, repository_id, branch, token)
    lease.heartbeat_at = utcnow()
    lease.expires_at = lease.heartbeat_at + timedelta(seconds=ttl_seconds)


def assert_current(
    session: Session, *, repository_id: uuid.UUID, branch: str, token: int
) -> None:
    """Called by the Git broker before every push and by the result API before finalizing."""
    _active_for_token(session, repository_id, branch, token)


def release(session: Session, *, task_id: uuid.UUID, reason: str) -> int:
    now = utcnow()
    leases = session.scalars(
        select(BranchLease)
        .where(BranchLease.task_id == task_id, BranchLease.released_at.is_(None))
        .with_for_update()
    ).all()
    for lease in leases:
        lease.released_at = now
        lease.release_reason = reason
    return len(leases)


def _active_for_token(
    session: Session, repository_id: uuid.UUID, branch: str, token: int
) -> BranchLease:
    lease = session.scalar(
        select(BranchLease)
        .where(
            BranchLease.repository_id == repository_id,
            BranchLease.branch == branch,
            BranchLease.fencing_token == token,
        )
        .with_for_update()
    )
    if lease is None or lease.released_at is not None or lease.expires_at <= utcnow():
        raise Conflict("stale or unknown fencing token", code="lease_fenced")
    return lease
