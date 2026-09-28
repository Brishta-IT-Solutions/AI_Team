"""FR-14 state machine — pure, no database."""

import pytest

from control_api.domain.lifecycle import (
    KANBAN_COLUMN_FOR_STAGE,
    KANBAN_COLUMNS,
    ExecutionStatus,
    GuardFacts,
    Stage,
    Trigger,
    evaluate,
    transition_table,
)

OPEN = GuardFacts(
    project_active=True, inputs_valid=True, reason="because", spec_present=True,
    spec_immutable=True, dependencies_done=True, budget_reserved=True, lease_acquired=True,
    scope_valid=True, self_checks_passed=True, qa_mandatory_all_pass=True,
    qa_evidence_current=True, functional_failure_with_defects=True, ci_required_pass=True,
    merge_confirmed_by_github=True,
)

FRD_TABLE = [
    (Stage.NEW, Trigger.START_ANALYSIS, Stage.BA_ANALYSIS),
    (Stage.BA_ANALYSIS, Trigger.BA_RESULT_VALID, Stage.REQUIREMENTS_APPROVAL),
    (Stage.REQUIREMENTS_APPROVAL, Trigger.REQUEST_REQUIREMENT_CHANGES, Stage.BA_ANALYSIS),
    (Stage.REQUIREMENTS_APPROVAL, Trigger.APPROVE_REQUIREMENTS, Stage.READY_FOR_DEV),
    (Stage.READY_FOR_DEV, Trigger.START_DEVELOPMENT, Stage.DEVELOPING),
    (Stage.DEVELOPING, Trigger.SUBMIT_DEVELOPMENT, Stage.DEV_REVIEW),
    (Stage.DEV_REVIEW, Trigger.DEV_REVIEW_PASSED, Stage.QA),
    (Stage.DEV_REVIEW, Trigger.DEV_REVIEW_FAILED, Stage.FIX_REQUIRED),
    (Stage.QA, Trigger.QA_PASSED, Stage.MERGE_APPROVAL),
    (Stage.QA, Trigger.QA_FAILED, Stage.FIX_REQUIRED),
    (Stage.FIX_REQUIRED, Trigger.START_REPAIR, Stage.DEVELOPING),
    (Stage.MERGE_APPROVAL, Trigger.REQUEST_CODE_CHANGES, Stage.FIX_REQUIRED),
    (Stage.MERGE_APPROVAL, Trigger.APPROVE_MERGE, Stage.MERGING),
    (Stage.MERGE_APPROVAL, Trigger.HEAD_OR_BASE_CHANGED, Stage.QA),
    (Stage.MERGING, Trigger.MERGE_CONFIRMED, Stage.DONE),
]


@pytest.mark.parametrize(("src", "trigger", "dst"), FRD_TABLE)
def test_every_frd_transition_is_legal_when_guards_hold(src, trigger, dst):
    d = evaluate(src, trigger, OPEN)
    assert d.allowed, d.unmet
    assert d.to_stage is dst


def test_table_contains_nothing_beyond_frd_plus_cancel_and_requirement_changes():
    extra = {(s, t, d) for s, t, d in transition_table()} - set(FRD_TABLE)
    assert {t for _, t, _ in extra} == {Trigger.CANCEL, Trigger.REQUIREMENTS_CHANGED}
    assert all(d is Stage.CANCELLED for _, t, d in extra if t is Trigger.CANCEL)
    assert all(d is Stage.BA_ANALYSIS for _, t, d in extra if t is Trigger.REQUIREMENTS_CHANGED)


@pytest.mark.parametrize("terminal", [Stage.DONE, Stage.CANCELLED])
@pytest.mark.parametrize("trigger", list(Trigger))
def test_terminal_stages_accept_nothing(terminal, trigger):
    assert not evaluate(terminal, trigger, OPEN).allowed


def test_merging_freezes_cancellation():
    d = evaluate(Stage.MERGING, Trigger.CANCEL, OPEN)
    assert not d.allowed


def test_cannot_jump_to_done():  # AT-17 at the domain level
    for stage in Stage:
        if stage is not Stage.MERGING:
            assert not any(
                evaluate(stage, t, OPEN).to_stage is Stage.DONE and evaluate(stage, t, OPEN).allowed
                for t in Trigger
            )


def test_blocking_questions_block_requirements_approval():  # AT-02
    d = evaluate(Stage.REQUIREMENTS_APPROVAL, Trigger.APPROVE_REQUIREMENTS,
                 GuardFacts(spec_present=True, blocking_questions_open=2))
    assert not d.allowed
    assert "2 blocking question(s) unresolved" in d.unmet


def test_unmet_prerequisites_are_all_reported():
    d = evaluate(Stage.READY_FOR_DEV, Trigger.START_DEVELOPMENT, GuardFacts())
    assert len(d.unmet) == 4


def test_request_changes_requires_reason():
    d = evaluate(Stage.REQUIREMENTS_APPROVAL, Trigger.REQUEST_REQUIREMENT_CHANGES, GuardFacts())
    assert not d.allowed and "a reason is required" in d.unmet


def test_cancel_waits_for_external_effects():
    facts = GuardFacts(reason="stop", pending_external_effects=True)
    assert not evaluate(Stage.DEVELOPING, Trigger.CANCEL, facts).allowed


def test_third_failed_repair_pauses():  # AT-11
    from dataclasses import replace
    d = evaluate(Stage.QA, Trigger.QA_FAILED, replace(OPEN, repair_count=3, repair_limit=3))
    assert d.allowed and d.to_stage is Stage.FIX_REQUIRED and d.status is ExecutionStatus.PAUSED
    d = evaluate(Stage.FIX_REQUIRED, Trigger.START_REPAIR, replace(OPEN, repair_count=3,
                                                                    repair_limit=3))
    assert not d.allowed and "human resume required" in d.explanation
    d = evaluate(Stage.QA, Trigger.QA_FAILED, replace(OPEN, repair_count=2, repair_limit=3))
    assert d.status is ExecutionStatus.IDLE


def test_qa_pass_requires_current_evidence():
    from dataclasses import replace
    d = evaluate(Stage.QA, Trigger.QA_PASSED, replace(OPEN, qa_evidence_current=False))
    assert not d.allowed


def test_kanban_maps_every_stage_to_a_known_column():
    assert set(KANBAN_COLUMN_FOR_STAGE) == set(Stage)
    assert set(KANBAN_COLUMN_FOR_STAGE.values()) <= set(KANBAN_COLUMNS)
