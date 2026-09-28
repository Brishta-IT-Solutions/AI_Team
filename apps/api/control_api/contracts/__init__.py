"""Adapter and artifact contracts (FRD FR-20, FR-21).

Every model forbids unknown fields: these are security-sensitive boundaries where
model output enters the control plane. Validation checks structure and internal
consistency; it never trusts a model's claims about Git or test results.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from control_api.domain.permissions import AgentRole

SCHEMA_VERSION = "1.0"

Sha = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
StableId = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9]*-[0-9]{1,4}$", max_length=24)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


def _unique(ids: list[str], label: str) -> None:
    seen: set[str] = set()
    dupes = sorted({i for i in ids if i in seen or seen.add(i)})  # type: ignore[func-returns-value]
    if dupes:
        raise ValueError(f"duplicate {label} IDs: {', '.join(dupes)}")


# ---------------------------------------------------------------- common envelope


class ContextManifestEntry(Strict):
    path: str = Field(min_length=1, max_length=1024)
    sha256: Sha256


class InvokeRequest(Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    run_id: str
    task_id: str
    role: AgentRole
    spec_version: int = Field(ge=1)
    base_sha: Sha
    head_sha: Sha | None = None
    context_manifest: list[ContextManifestEntry]
    policy_version: int = Field(ge=1)
    config_version: int = Field(ge=1)
    output_schema: str
    deadline: datetime
    max_output_tokens: int = Field(gt=0, le=200_000)
    reservation_id: str


class RunEventType(StrEnum):
    STARTED = "STARTED"
    MILESTONE = "MILESTONE"
    LOG = "LOG"
    USAGE = "USAGE"
    HEARTBEAT = "HEARTBEAT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class RunEvent(Strict):
    event_id: str
    run_id: str
    attempt: int = Field(ge=1)
    sequence: int = Field(ge=0)
    timestamp: datetime
    type: RunEventType
    payload: dict = Field(default_factory=dict)  # sanitized before persistence


class UsageQuality(StrEnum):
    PROVIDER_ESTIMATE = "PROVIDER_ESTIMATE"
    CALCULATED_ESTIMATE = "CALCULATED_ESTIMATE"
    RECONCILED_ACTUAL = "RECONCILED_ACTUAL"
    UNKNOWN = "UNKNOWN"


class Usage(Strict):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    amount: str | None = Field(default=None, pattern=r"^\d+(\.\d{1,8})?$")  # decimal as text
    currency: Literal["USD"] = "USD"
    quality: UsageQuality = UsageQuality.UNKNOWN
    price_version: str | None = None


class Outcome(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class RunError(Strict):
    code: str
    message: str = Field(max_length=4000)
    retryable: bool = False


class Result(Strict):
    outcome: Outcome
    artifact_refs: list[Sha256] = Field(default_factory=list)
    usage: Usage
    errors: list[RunError] = Field(default_factory=list)
    envelope: InvokeRequest


# ---------------------------------------------------------------- BA specification


class Story(Strict):
    id: StableId
    as_a: str = Field(min_length=1, max_length=500)
    i_want: str = Field(min_length=1, max_length=2000)
    so_that: str = Field(min_length=1, max_length=2000)


class BusinessRule(Strict):
    id: StableId
    statement: str = Field(min_length=1, max_length=4000)


class AcceptanceCriterion(Strict):
    id: StableId
    statement: str = Field(min_length=1, max_length=4000)
    verification: str = Field(min_length=1, max_length=4000)
    mandatory: bool


class UxFlow(Strict):
    id: StableId
    name: str = Field(min_length=1, max_length=200)
    steps: list[str] = Field(min_length=1)


class Question(Strict):
    id: StableId
    text: str = Field(min_length=1, max_length=4000)
    blocking: bool
    resolution: str | None = Field(default=None, max_length=4000)

    @property
    def open_blocking(self) -> bool:
        return self.blocking and not (self.resolution and self.resolution.strip())


class BASpecification(Strict):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    goal: str = Field(min_length=1, max_length=4000)
    stories: list[Story] = Field(min_length=1)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(min_length=1)
    ux_flows: list[UxFlow] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    questions: list[Question] = Field(default_factory=list)

    @model_validator(mode="after")
    def _consistency(self) -> BASpecification:
        if not any(ac.mandatory for ac in self.acceptance_criteria):
            raise ValueError("at least one mandatory acceptance criterion is required")
        _unique([s.id for s in self.stories], "story")
        _unique([r.id for r in self.business_rules], "business rule")
        _unique([a.id for a in self.acceptance_criteria], "acceptance criterion")
        _unique([f.id for f in self.ux_flows], "UX flow")
        _unique([q.id for q in self.questions], "question")
        return self

    @property
    def open_blocking_questions(self) -> list[Question]:
        return [q for q in self.questions if q.open_blocking]


# ---------------------------------------------------------------- developer submission


class TestEvidence(Strict):
    command_id: str
    exit_code: int
    artifact_ref: Sha256


class DeveloperSubmission(Strict):
    head_sha: Sha
    base_sha: Sha
    files_changed: list[str] = Field(min_length=1)
    summary: str = Field(min_length=1, max_length=8000)
    ux_artifacts: list[Sha256] = Field(default_factory=list)
    tests: list[TestEvidence] = Field(default_factory=list)
    known_limitations: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------- QA report


class CriterionResult(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NOT_RUN = "NOT_RUN"


class CriterionOutcome(Strict):
    ac_id: StableId
    result: CriterionResult
    evidence_refs: list[Sha256] = Field(default_factory=list)


class SuiteOutcome(Strict):
    name: str
    mandatory: bool = True
    result: CriterionResult
    evidence_refs: list[Sha256] = Field(default_factory=list)


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Finding(Strict):
    ac_id: StableId | None = None
    severity: Severity
    title: str = Field(min_length=1, max_length=300)
    reproduction_steps: list[str] = Field(min_length=1)
    expected: str
    actual: str
    test_command: str | None = None


class QAReport(Strict):
    spec_version: int = Field(ge=1)
    head_sha: Sha
    base_sha: Sha
    criteria_results: list[CriterionOutcome]
    suites: list[SuiteOutcome] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    overall_result: CriterionResult

    @model_validator(mode="after")
    def _deterministic_verdict(self) -> QAReport:
        _unique([c.ac_id for c in self.criteria_results], "criterion result")
        expected = compute_overall(self.criteria_results, self.suites)
        if self.overall_result is not expected:
            raise ValueError(
                f"overall_result {self.overall_result} contradicts results (expected {expected})"
            )
        return self


def compute_overall(
    criteria: list[CriterionOutcome], suites: list[SuiteOutcome]
) -> CriterionResult:
    """FAIL beats BLOCKED beats NOT_RUN beats PASS. Anything short of all-PASS is not PASS."""
    results = [c.result for c in criteria] + [s.result for s in suites if s.mandatory]
    for verdict in (CriterionResult.FAIL, CriterionResult.BLOCKED, CriterionResult.NOT_RUN):
        if verdict in results:
            return verdict
    return CriterionResult.PASS if results else CriterionResult.NOT_RUN


def check_qa_coverage(
    report: QAReport, approved_ac_ids: set[str], *, spec_version: int, head_sha: str, base_sha: str
) -> list[str]:
    """Return problems that make the report unusable for the current evidence tuple (AT-07)."""
    problems: list[str] = []
    reported = {c.ac_id for c in report.criteria_results}
    if missing := sorted(approved_ac_ids - reported):
        problems.append(f"missing criteria: {', '.join(missing)}")
    if extra := sorted(reported - approved_ac_ids):
        problems.append(f"unknown criteria: {', '.join(extra)}")
    if report.spec_version != spec_version:
        problems.append(f"report is for spec v{report.spec_version}, current is v{spec_version}")
    if report.head_sha != head_sha:
        problems.append("report head SHA does not match current head")
    if report.base_sha != base_sha:
        problems.append("report base SHA does not match current base")
    return problems


# ---------------------------------------------------------------- junior (Ollama) assignment


class JuniorTaskType(StrEnum):
    MOCK_DATA = "MOCK_DATA"
    TYPES = "TYPES"
    DOCUMENTATION = "DOCUMENTATION"
    SIMPLE_TESTS = "SIMPLE_TESTS"
    MECHANICAL_RENAME = "MECHANICAL_RENAME"
    FORMATTING = "FORMATTING"


class JuniorAssignment(Strict):
    parent_run_id: str
    task_type: JuniorTaskType
    complexity: int = Field(ge=1, le=5)
    allowed_paths: list[str] = Field(min_length=1)
    input_refs: list[Sha256] = Field(default_factory=list)
    expected_output: str = Field(min_length=1, max_length=4000)
    checks: list[str] = Field(default_factory=list)
    deadline: datetime


class JuniorResult(Strict):
    patch_ref: Sha256
    files_touched: list[str]
    lines_changed: int = Field(ge=0)
    check_evidence: list[TestEvidence] = Field(default_factory=list)
