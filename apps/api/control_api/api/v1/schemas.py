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
    command: Literal["analyze", "request_changes", "cancel", "resume", "retry", "delete"]
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


class CommandSpec(In):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,39}$")
    argv: list[str] = Field(min_length=1, max_length=20)
    timeout_seconds: int = Field(default=600, ge=10, le=3600)
    required: bool = True


class ExecutionPolicy(In):
    """Engineering-approved commands (argv, never a shell string) and protected paths (FR-04)."""

    commands: list[CommandSpec] = Field(max_length=10)
    protected_paths: list[str] = Field(default_factory=list, max_length=50)
    expected_version: int


class WorkerHello(In):
    worker_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")
    version: str | None = Field(default=None, max_length=40)
    capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)


class WorkerClaim(In):
    worker_id: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,120}$")
    roles: list[Literal["BA", "DEVELOPER", "QA"]] = Field(min_length=1)


class RunHeartbeat(In):
    claim_token: str = Field(min_length=16, max_length=64)
    milestone: str | None = Field(default=None, max_length=200)
    logs: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


class JuniorRequest(In):
    claim_token: str = Field(min_length=16, max_length=64)
    assignments: list[dict[str, Any]] = Field(min_length=1, max_length=10)


class RunResultBody(In):
    claim_token: str = Field(min_length=16, max_length=64)
    outcome: Literal["SUCCEEDED", "FAILED", "BLOCKED"]
    payload: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] = Field(default_factory=dict)
    errors: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    provider: str | None = Field(default=None, max_length=40)
    model: str | None = Field(default=None, max_length=200)
