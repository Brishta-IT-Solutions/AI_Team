from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from control_api.domain.permissions import Principal
from control_api.errors import ApiError
from control_api.services import idempotency
from control_api.services.context import Ctx


def make_ctx(request: Request, session: Session, principal: Principal) -> Ctx:
    return Ctx(session=session, principal=principal, correlation_id=request.state.correlation_id)


def mutate(
    request: Request,
    session: Session,
    principal: Principal,
    body: Any,
    fn: Callable[[], tuple[int, dict[str, Any]]],
) -> JSONResponse:
    """Run a mutation once per Idempotency-Key, committing the outcome with the change."""
    key = request.headers.get("idempotency-key", "")
    fp = idempotency.fingerprint(request.method, request.url.path, body)
    existing = idempotency.lookup(session, principal.id, key, fp)
    if existing is not None:
        return _replay(existing.status_code, existing.response)
    try:
        status_code, payload = fn()
        if not idempotency.store(session, principal.id, key, fp, status_code, payload):
            session.rollback()  # a concurrent duplicate committed first; return its outcome
            winner = idempotency.lookup(session, principal.id, key, fp)
            assert winner is not None
            return _replay(winner.status_code, winner.response)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return JSONResponse(payload, status_code=status_code)


def _replay(status_code: int, payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload, status_code=status_code, headers={"Idempotent-Replay": "true"})


def encode_cursor(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise ApiError("malformed cursor", code="bad_cursor") from exc
