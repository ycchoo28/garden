"""The approval rules as pure functions: which steps apply, the run state, and who may decide."""

import uuid
from decimal import Decimal

from app.core.approvals.workflow import (
    Approver,
    Decided,
    Outcome,
    RunState,
    Step,
    current_step,
    refusal_for,
    state_of,
    steps_for,
)

DEPARTMENT = uuid.UUID("0190f0a0-0000-7000-8000-00000000000d")
OTHER_DEPARTMENT = uuid.UUID("0190f0a0-0000-7000-8000-00000000000e")
STARTER = uuid.UUID("0190f0a0-0000-7000-8000-000000000001")
MANAGER = uuid.UUID("0190f0a0-0000-7000-8000-000000000002")

MANAGER_STEP = Step(position=1, role="manager", department_scoped=True, above=Decimal("0.00"))
FINANCE_STEP = Step(position=2, role="finance", department_scoped=False, above=Decimal("500.00"))
STEPS = (MANAGER_STEP, FINANCE_STEP)


def _approver(person: uuid.UUID, roles: frozenset[str], department: uuid.UUID) -> Approver:
    return Approver(id=person, roles=roles, department_id=department)


def test_a_step_applies_only_above_its_threshold() -> None:
    assert [step.position for step in steps_for(STEPS, Decimal("500.00"))] == [1]
    assert [step.position for step in steps_for(STEPS, Decimal("500.01"))] == [1, 2]
    assert [step.position for step in steps_for(STEPS, Decimal("0.01"))] == [1]


def test_state_moves_from_pending_to_approved_through_every_step() -> None:
    assert state_of(STEPS, [], cancelled=False) is RunState.PENDING
    first = [Decided(1, Outcome.APPROVED)]
    assert state_of(STEPS, first, cancelled=False) is RunState.PENDING
    assert current_step(STEPS, first) == FINANCE_STEP
    both = [*first, Decided(2, Outcome.APPROVED)]
    assert state_of(STEPS, both, cancelled=False) is RunState.APPROVED
    assert current_step(STEPS, both) is None


def test_a_rejection_ends_the_run_and_a_withdrawal_cancels_it() -> None:
    rejected = [Decided(1, Outcome.REJECTED)]
    assert state_of(STEPS, rejected, cancelled=False) is RunState.REJECTED
    assert state_of(STEPS, [], cancelled=True) is RunState.CANCELLED


def test_the_claimant_never_decides_their_own_request_whatever_their_roles() -> None:
    boss = _approver(STARTER, frozenset({"manager", "finance"}), DEPARTMENT)
    reason = refusal_for(
        MANAGER_STEP,
        boss,
        started_by=STARTER,
        subject_department_id=DEPARTMENT,
        earlier_deciders=frozenset(),
    )
    assert reason == "Nobody can decide their own request."


def test_the_same_person_cannot_decide_two_steps_of_one_run() -> None:
    both_roles = _approver(MANAGER, frozenset({"manager", "finance"}), DEPARTMENT)
    reason = refusal_for(
        FINANCE_STEP,
        both_roles,
        started_by=STARTER,
        subject_department_id=DEPARTMENT,
        earlier_deciders=frozenset({MANAGER}),
    )
    assert reason == "You already decided an earlier step of this request."


def test_a_step_needs_its_role_and_a_department_scoped_step_needs_the_same_department() -> None:
    employee = _approver(MANAGER, frozenset({"employee"}), DEPARTMENT)
    assert refusal_for(
        MANAGER_STEP,
        employee,
        started_by=STARTER,
        subject_department_id=DEPARTMENT,
        earlier_deciders=frozenset(),
    ) == ("Only a manager can decide this step.")
    other_manager = _approver(MANAGER, frozenset({"manager"}), OTHER_DEPARTMENT)
    assert refusal_for(
        MANAGER_STEP,
        other_manager,
        started_by=STARTER,
        subject_department_id=DEPARTMENT,
        earlier_deciders=frozenset(),
    ) == ("This request belongs to another department.")


def test_finance_may_decide_the_unscoped_step_from_any_department() -> None:
    finance = _approver(MANAGER, frozenset({"finance"}), OTHER_DEPARTMENT)
    assert (
        refusal_for(
            FINANCE_STEP,
            finance,
            started_by=STARTER,
            subject_department_id=DEPARTMENT,
            earlier_deciders=frozenset({STARTER}),
        )
        is None
    )
