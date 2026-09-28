"""Idempotency-Key handling (FR-23).

Same key + same body returns the stored outcome. Same key + different body is 409.
The record is written in the same transaction as the mutation it protects.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from control_api.db.models import IdempotencyRecord
from control_api.domain.approvals import canonical_hash
from control_api.errors import ApiError, Conflict

MAX_KEY_LENGTH = 200


def fingerprint(method: str, path: str, body: Any) -> str:
    return canonical_hash({"method": method, "path": path, "body": body})


def lookup(session: Session, principal_id: str, key: str, fp: str) -> IdempotencyRecord | None:
    if not key or len(key) > MAX_KEY_LENGTH:
        raise ApiError("Idempotency-Key header is required (1-200 chars)", code="idempotency_key")
    record = session.scalar(
        select(IdempotencyRecord).where(
            IdempotencyRecord.principal_id == principal_id, IdempotencyRecord.key == key
        )
    )
    if record is not None and record.fingerprint != fp:
        raise Conflict(
            "Idempotency-Key was already used with a different request", code="idempotency_reuse"
        )
    return record


def store(
    session: Session, principal_id: str, key: str, fp: str, status_code: int, response: Any
) -> bool:
    """Insert the outcome. Returns False if a concurrent request won the race."""
    result = session.execute(
        insert(IdempotencyRecord)
        .values(
            principal_id=principal_id,
            key=key,
            fingerprint=fp,
            status_code=status_code,
            response=response,
        )
        .on_conflict_do_nothing()
        .returning(IdempotencyRecord.key)
    )
    return result.first() is not None
