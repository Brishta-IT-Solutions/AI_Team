"""Principal resolution.

Production sign-in is OIDC with HttpOnly cookies (FR-27) and short-lived scoped
service identities for workers. Until the identity provider is selected, a
development bearer scheme stands in. It is refused when AITC_ENV=production.

    Authorization: Bearer dev-human:<subject>
    Authorization: Bearer dev-agent:<ROLE>:<project_id>:<run_id>
    Authorization: Bearer dev-service:<name>:<project_id>
"""

from __future__ import annotations

import uuid

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_api.config import get_settings
from control_api.db.models import Membership, User
from control_api.db.session import get_session
from control_api.domain.permissions import AgentRole, HumanRole, Principal, PrincipalKind
from control_api.errors import Unauthenticated


def load_human(session: Session, user: User) -> Principal:
    rows = session.scalars(select(Membership).where(Membership.user_id == user.id)).all()
    return Principal(
        kind=PrincipalKind.HUMAN,
        id=str(user.id),
        active=user.active,
        workspace_admin=user.workspace_admin,
        project_roles={str(m.project_id): frozenset(HumanRole(r) for r in m.roles) for m in rows},
    )


def _parse_dev_token(session: Session, token: str) -> Principal:
    kind, _, rest = token.partition(":")
    if kind == "dev-human" and rest:
        user = session.scalar(select(User).where(User.subject == rest))
        if user is None:
            raise Unauthenticated("unknown subject")
        return load_human(session, user)
    if kind == "dev-agent":
        role, project_id, run_id = (rest.split(":") + ["", "", ""])[:3]
        try:
            agent_role = AgentRole(role)
            uuid.UUID(project_id)
        except ValueError as exc:
            raise Unauthenticated("malformed agent token") from exc
        return Principal(
            kind=PrincipalKind.AGENT,
            id=f"agent:{agent_role}:{run_id or 'unbound'}",
            agent_role=agent_role,
            scoped_project_id=project_id,
        )
    if kind == "dev-service":
        name, _, project_id = rest.partition(":")
        if not name:
            raise Unauthenticated("malformed service token")
        return Principal(
            kind=PrincipalKind.SERVICE, id=f"service:{name}", scoped_project_id=project_id or None
        )
    raise Unauthenticated("unsupported token")


def current_principal(
    request: Request, session: Session = Depends(get_session)
) -> Principal:
    settings = get_settings()
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise Unauthenticated("missing bearer credentials")
    if settings.dev_auth_enabled and token.startswith("dev-"):
        return _parse_dev_token(session, token)
    # TODO(FR-27): verify OIDC session cookie / signed workload identity here.
    raise Unauthenticated("no production authenticator configured")
