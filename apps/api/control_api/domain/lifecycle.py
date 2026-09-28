"""Task lifecycle (FRD FR-14).

Stage and execution status are stored separately. This module is pure: it knows
nothing about the database. Callers gather `GuardFacts` from persisted state and
ask `evaluate()` whether a trigger is legal. Every refusal carries the exact unmet
prerequisites so the UI can explain a disabled action (PRD §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Stage(StrEnum):
    NEW = "NEW"
    BA_ANALYSIS = "BA_ANALYSIS"
    REQUIREMENTS_APPROVAL = "REQUIREMENTS_APPROVAL"
    READY_FOR_DEV = "READY_FOR_DEV"
    DEVELOPING = "DEVELOPING"
    DEV_REVIEW = "DEV_REVIEW"
    QA = "QA"
    FIX_REQUIRED = "FIX_REQUIRED"
    MERGE_APPROVAL = "MERGE_APPROVAL"
    MERGING = "MERGING"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


class ExecutionStatus(StrEnum):
    IDLE = "IDLE"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"


class Trigger(StrEnum):
    START_ANALYSIS = "START_ANALYSIS"
    BA_RESULT_VALID = "BA_RESULT_VALID"
    REQUEST_REQUIREMENT_CHANGES = "REQUEST_REQUIREMENT_CHANGES"
    APPROVE_REQUIREMENTS = "APPROVE_REQUIREMENTS"
    START_DEVELOPMENT = "START_DEVELOPMENT"
    SUBMIT_DEVELOPMENT = "SUBMIT_DEVELOPMENT"
    DEV_REVIEW_PASSED = "DEV_REVIEW_PASSED"
    DEV_REVIEW_FAILED = "DEV_REVIEW_FAILED"
    QA_PASSED = "QA_PASSED"
    QA_FAILED = "QA_FAILED"
    START_REPAIR = "START_REPAIR"
    REQUEST_CODE_CHANGES = "REQUEST_CODE_CHANGES"
    APPROVE_MERGE = "APPROVE_MERGE"
    MERGE_CONFIRMED = "MERGE_CONFIRMED"
    REQUIREMENTS_CHANGED = "REQUIREMENTS_CHANGED"
    HEAD_OR_BASE_CHANGED = "HEAD_OR_BASE_CHANGED"
    CANCEL = "CANCEL"


TERMINAL_STAGES = frozenset({Stage.DONE, Stage.CANCELLED})

# Stages from which an approved-requirements edit sends work back to analysis.
PRE_MERGE_STAGES = frozenset(
    {
        Stage.REQUIREMENTS_APPROVAL,
        Stage.READY_FOR_DEV,
        Stage.DEVELOPING,
        Stage.DEV_REVIEW,
        Stage.QA,
        Stage.FIX_REQUIRED,
        Stage.MERGE_APPROVAL,
    }
)

DEFAULT_REPAIR_LIMIT = 3


@dataclass(frozen=True)
class GuardFacts:
    """Facts the guards need. Gathered from persisted state, never from the client."""

    project_active: bool = False
    inputs_valid: bool = False
    reason: str | None = None
    spec_present: bool = False
    spec_immutable: bool = False
    blocking_questions_open: int = 0
    dependencies_done: bool = False
    budget_reserved: bool = False
    lease_acquired: bool = False
    scope_valid: bool = False
    self_checks_passed: bool = False
    qa_mandatory_all_pass: bool = False
    qa_evidence_current: bool = False
    functional_failure_with_defects: bool = False
    repair_count: int = 0
    repair_limit: int = DEFAULT_REPAIR_LIMIT
    ci_required_pass: bool = False
    merge_confirmed_by_github: bool = False
    merge_executor_available: bool = False
    pending_external_effects: bool = False


@dataclass(frozen=True)
class Decision:
    allowed: bool
    trigger: Trigger
    from_stage: Stage
    to_stage: Stage | None
    unmet: tuple[str, ...] = field(default_factory=tuple)
    status: ExecutionStatus | None = None

    @property
    def explanation(self) -> str:
        return "; ".join(self.unmet)


def _need(cond: bool, message: str, unmet: list[str]) -> None:
    if not cond:
        unmet.append(message)


def _reason(f: GuardFacts, unmet: list[str]) -> None:
    _need(bool(f.reason and f.reason.strip()), "a reason is required", unmet)


def _g_start_analysis(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.project_active, "project is not active", unmet)
    _need(f.inputs_valid, "ticket inputs are invalid", unmet)


def _g_ba_result(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.spec_present, "BA result has not produced a valid specification", unmet)


def _g_approve_requirements(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.spec_present, "no specification version to approve", unmet)
    _need(
        f.blocking_questions_open == 0,
        f"{f.blocking_questions_open} blocking question(s) unresolved",
        unmet,
    )


def _g_start_development(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.spec_immutable, "specification is not approved", unmet)
    _need(f.dependencies_done, "dependencies are not DONE", unmet)
    _need(f.budget_reserved, "budget reservation not held", unmet)
    _need(f.lease_acquired, "branch write lease not acquired", unmet)


def _g_dev_review_passed(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.scope_valid, "broker scope validation failed", unmet)
    _need(f.self_checks_passed, "mandatory self-checks did not pass", unmet)


def _g_qa_passed(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.qa_mandatory_all_pass, "not all mandatory criteria and suites passed", unmet)
    _need(f.qa_evidence_current, "QA evidence does not match the current head/base", unmet)


def _g_qa_failed(f: GuardFacts, unmet: list[str]) -> None:
    _need(
        f.functional_failure_with_defects,
        "no valid functional failure with linked defects",
        unmet,
    )


def _g_start_repair(f: GuardFacts, unmet: list[str]) -> None:
    _need(
        f.repair_count < f.repair_limit,
        f"repair allowance exhausted ({f.repair_count}/{f.repair_limit}); human resume required",
        unmet,
    )
    _need(f.budget_reserved, "budget reservation not held", unmet)
    _need(f.lease_acquired, "branch write lease not acquired", unmet)


def _g_approve_merge(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.qa_mandatory_all_pass, "QA has not passed", unmet)
    _need(f.qa_evidence_current, "QA evidence is stale for the current head/base", unmet)
    _need(f.ci_required_pass, "required CI checks have not passed", unmet)
    _need(f.merge_executor_available,
          "Git broker is not connected yet; merge the QA-passed branch manually for now", unmet)


def _g_merge_confirmed(f: GuardFacts, unmet: list[str]) -> None:
    _need(f.merge_confirmed_by_github, "GitHub has not confirmed the approved merge", unmet)


def _g_cancel(f: GuardFacts, unmet: list[str]) -> None:
    _need(not f.pending_external_effects, "pending external effects must be reconciled", unmet)


def _g_none(f: GuardFacts, unmet: list[str]) -> None:
    return None


# (from, trigger) -> (to, guard, requires_reason)
_TABLE: dict[tuple[Stage, Trigger], tuple[Stage, object, bool]] = {
    (Stage.NEW, Trigger.START_ANALYSIS): (Stage.BA_ANALYSIS, _g_start_analysis, False),
    (Stage.BA_ANALYSIS, Trigger.BA_RESULT_VALID): (
        Stage.REQUIREMENTS_APPROVAL, _g_ba_result, False),
    (Stage.REQUIREMENTS_APPROVAL, Trigger.REQUEST_REQUIREMENT_CHANGES): (
        Stage.BA_ANALYSIS, _g_none, True),
    (Stage.REQUIREMENTS_APPROVAL, Trigger.APPROVE_REQUIREMENTS): (
        Stage.READY_FOR_DEV, _g_approve_requirements, False),
    (Stage.READY_FOR_DEV, Trigger.START_DEVELOPMENT): (
        Stage.DEVELOPING, _g_start_development, False),
    (Stage.DEVELOPING, Trigger.SUBMIT_DEVELOPMENT): (Stage.DEV_REVIEW, _g_none, False),
    (Stage.DEV_REVIEW, Trigger.DEV_REVIEW_PASSED): (Stage.QA, _g_dev_review_passed, False),
    (Stage.DEV_REVIEW, Trigger.DEV_REVIEW_FAILED): (Stage.FIX_REQUIRED, _g_none, False),
    (Stage.QA, Trigger.QA_PASSED): (Stage.MERGE_APPROVAL, _g_qa_passed, False),
    (Stage.QA, Trigger.QA_FAILED): (Stage.FIX_REQUIRED, _g_qa_failed, False),
    (Stage.FIX_REQUIRED, Trigger.START_REPAIR): (Stage.DEVELOPING, _g_start_repair, False),
    (Stage.MERGE_APPROVAL, Trigger.REQUEST_CODE_CHANGES): (Stage.FIX_REQUIRED, _g_none, True),
    (Stage.MERGE_APPROVAL, Trigger.APPROVE_MERGE): (Stage.MERGING, _g_approve_merge, False),
    (Stage.MERGE_APPROVAL, Trigger.HEAD_OR_BASE_CHANGED): (Stage.QA, _g_none, False),
    (Stage.MERGING, Trigger.MERGE_CONFIRMED): (Stage.DONE, _g_merge_confirmed, False),
}

for _stage in PRE_MERGE_STAGES:
    _TABLE[(_stage, Trigger.REQUIREMENTS_CHANGED)] = (Stage.BA_ANALYSIS, _g_none, False)

# MERGING freezes cancellation until the remote outcome is known (FR-14).
for _stage in Stage:
    if _stage not in TERMINAL_STAGES and _stage is not Stage.MERGING:
        _TABLE[(_stage, Trigger.CANCEL)] = (Stage.CANCELLED, _g_cancel, True)


def evaluate(stage: Stage, trigger: Trigger, facts: GuardFacts) -> Decision:
    """Decide a transition. Never raises for illegal input; returns a refusal."""
    if stage in TERMINAL_STAGES:
        return Decision(False, trigger, stage, None, (f"{stage} is terminal; open a linked ticket",))
    entry = _TABLE.get((stage, trigger))
    if entry is None:
        return Decision(False, trigger, stage, None, (f"{trigger} is not permitted from {stage}",))
    to_stage, guard, requires_reason = entry
    unmet: list[str] = []
    if requires_reason:
        _reason(facts, unmet)
    guard(facts, unmet)  # type: ignore[operator]
    if unmet:
        return Decision(False, trigger, stage, to_stage, tuple(unmet))
    return Decision(True, trigger, stage, to_stage, (), _status_after(trigger, facts))


def _status_after(trigger: Trigger, facts: GuardFacts) -> ExecutionStatus:
    # Three failed repair cycles pause the ticket for human intervention (PRD §3).
    if trigger is Trigger.QA_FAILED and facts.repair_count >= facts.repair_limit:
        return ExecutionStatus.PAUSED
    if trigger in {Trigger.START_ANALYSIS, Trigger.START_DEVELOPMENT, Trigger.START_REPAIR,
                   Trigger.DEV_REVIEW_PASSED, Trigger.HEAD_OR_BASE_CHANGED,
                   Trigger.REQUEST_REQUIREMENT_CHANGES, Trigger.REQUIREMENTS_CHANGED,
                   Trigger.APPROVE_MERGE}:
        return ExecutionStatus.QUEUED
    return ExecutionStatus.IDLE


def triggers_from(stage: Stage) -> list[Trigger]:
    return [t for (s, t) in _TABLE if s is stage]


def transition_table() -> list[tuple[Stage, Trigger, Stage]]:
    return [(s, t, v[0]) for (s, t), v in _TABLE.items()]


# Kanban columns (FR-08). Paused/failed/blocked are badges, never columns.
KANBAN_COLUMNS: tuple[str, ...] = (
    "Backlog", "Analysis", "Approval", "Development", "QA", "Merge", "Done",
)
KANBAN_COLUMN_FOR_STAGE: dict[Stage, str] = {
    Stage.NEW: "Backlog",
    Stage.BA_ANALYSIS: "Analysis",
    Stage.REQUIREMENTS_APPROVAL: "Approval",
    Stage.READY_FOR_DEV: "Development",
    Stage.DEVELOPING: "Development",
    Stage.DEV_REVIEW: "Development",
    Stage.FIX_REQUIRED: "Development",
    Stage.QA: "QA",
    Stage.MERGE_APPROVAL: "Merge",
    Stage.MERGING: "Merge",
    Stage.DONE: "Done",
    Stage.CANCELLED: "Done",  # rendered with a "cancelled" badge
}
