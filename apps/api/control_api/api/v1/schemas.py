from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from control_api.domain.approvals import ApprovalDecision, Gate
from control_api.domain.permissions import AgentRole, HumanRole


class In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(In):
    key: str = Field(pattern=r"^[A-Z][A-Z0-9]{1,9}$")
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    classification: Literal["SYNTHETIC", "SANITIZED", "INTERNAL", "CONFIDENTIAL"]


class MembershipSet(In):
    subject: str = Field(min_length=1, max_length=255)
    roles: list[HumanRole] = Field(min_length=1)


class RepositoryLink(In):
    installation_id: int = Field(gt=0)
    repository_id: int = Field(gt=0)
    base_branch: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9._/\-]+$")


class AgentConfigCreate(In):
    role: AgentRole
    provider: Literal["gemini", "antigravity", "claude_code", "codex", "ollama"]
    model: str = Field(min_length=1, max_length=200)
    endpoint_ref: str | None = Field(default=None, max_length=500)
    auth_method: Literal["api_key_ref", "oauth", "workload_identity", "local_bridge", "none"]
    adapter_version: str = Field(min_length=1, max_length=40)
    prompt_version: str = Field(min_length=1, max_length=40)
    prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    permissions: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any] = Field(default_factory=dict)
    secret_ref: str | None = Field(default=None, max_length=500, pattern=r"^(vault|sm|env)://.+")
    output_schema_version: str = "1.0"
    expected_version: int | None = Field(default=None, ge=0)


class BudgetSet(In):
    scope: Literal["PROJECT_MONTH", "TICKET", "RUN"]
    period: str = Field(default="*", pattern=r"^(\*|\d{4}-\d{2})$")
    cap: Decimal = Field(gt=0, max_digits=14, decimal_places=6)


class ActivateBody(In):
    expected_version: int


class TaskCreate(In):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=20_000)
    priority: Literal["P0", "P1", "P2", "P3"]
    dependencies: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class CommandBody(In):
    command: Literal["analyze", "request_changes", "cancel", "resume", "retry"]
    expected_version: int
    reason: str | None = Field(default=None, max_length=4000)
    additional_repairs: int | None = Field(default=None, ge=1, le=3)


class RequirementsBody(In):
    expected_version: int
    payload: dict[str, Any]
    source: Literal["AGENT", "MANUAL_IMPORT", "HUMAN_EDIT"] = "HUMAN_EDIT"
    provenance: dict[str, Any] = Field(default_factory=dict)


class ApprovalBody(In):
    gate: Gate
    decision: Literal[ApprovalDecision.APPROVED, ApprovalDecision.CHANGES_REQUESTED]
    scope_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_version: int
    reason: str | None = Field(default=None, max_length=4000)
