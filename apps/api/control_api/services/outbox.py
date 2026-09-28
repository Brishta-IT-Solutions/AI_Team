"""Transactional outbox (FR-02). Delivery is at least once; consumers deduplicate."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from control_api.db.models import OutboxEvent, ProcessedEvent, utcnow


def emit(
    session: Session,
    *,
    project_id: uuid.UUID,
    aggregate_type: str,
    aggregate_id: uuid.UUID | str,
    aggregate_version: int,
    type: str,
    payload: dict[str, Any],
) -> OutboxEvent:
    event = OutboxEvent(
        project_id=project_id,
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        aggregate_version=aggregate_version,
        type=type,
        payload=payload,
    )
    session.add(event)
    return event


class Publisher(Protocol):
    def publish(self, event: dict[str, Any]) -> None: ...


def envelope(e: OutboxEvent) -> dict[str, Any]:
    return {
        "event_id": str(e.event_id),
        "cursor": e.seq,
        "project_id": str(e.project_id),
        "aggregate_type": e.aggregate_type,
        "aggregate_id": e.aggregate_id,
        "aggregate_version": e.aggregate_version,
        "type": e.type,
        "payload": e.payload,
        "timestamp": e.created_at.isoformat(),
    }


def relay_once(factory: sessionmaker[Session], publisher: Publisher, batch_size: int = 100) -> int:
    """Publish undelivered events in order. Safe to run concurrently (SKIP LOCKED).

    A crash after publish but before commit re-publishes on restart; that is the
    at-least-once contract and why consumers must use `consume_once`.
    """
    with factory() as session, session.begin():
        rows = session.scalars(
            select(OutboxEvent)
            .where(OutboxEvent.delivered_at.is_(None))
            .order_by(OutboxEvent.seq)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        ).all()
        for row in rows:
            row.attempts += 1
            publisher.publish(envelope(row))
            row.delivered_at = utcnow()
        return len(rows)


def consume_once(
    session: Session, consumer: str, event_id: uuid.UUID | str, handler: Callable[[], None]
) -> bool:
    """Run `handler` at most once per (consumer, event_id), in the caller's transaction."""
    stmt = (
        insert(ProcessedEvent)
        .values(consumer=consumer, event_id=uuid.UUID(str(event_id)))
        .on_conflict_do_nothing()
        .returning(ProcessedEvent.event_id)
    )
    if session.execute(stmt).first() is None:
        return False
    handler()
    return True
