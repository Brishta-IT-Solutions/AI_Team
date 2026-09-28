from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from control_api.domain.permissions import Action, Principal, authorize
from control_api.errors import Forbidden, NotFound
from control_api.services.audit import record_denial


@dataclass
class Ctx:
    session: Session
    principal: Principal
    correlation_id: str

    def require(
        self,
        action: Action,
        project_id: uuid.UUID | str,
        *,
        object_type: str,
        object_id: uuid.UUID | str | None = None,
    ) -> None:
        """Deny by default. Non-members get a concealed 404; members get 403 with a reason."""
        pid = str(project_id)
        decision = authorize(self.principal, action, pid)
        if decision.allowed:
            return
        record_denial(
            self.principal,
            action=str(action),
            object_type=object_type,
            object_id=object_id,
            project_id=pid,
            correlation_id=self.correlation_id,
            reason=decision.reason,
        )
        if not self.principal.is_member(pid):
            raise NotFound(f"{object_type} not found")
        raise Forbidden(decision.reason, code="permission_denied")
