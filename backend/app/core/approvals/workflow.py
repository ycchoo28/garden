"""The approval state of one run. It is pure: no database and no HTTP, so every rule is unit-tested.

A run is one request for approval, started once for a subject. Its steps are a snapshot taken when
the run starts, so changing a flow later never changes a run that is already in progress.
"""

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum


class RunState(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class Outcome(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class Step:
    """One step of a flow. It applies to a run when the run's amount is above `above`."""

    position: int
    role: str
    department_scoped: bool
    above: Decimal


@dataclass(frozen=True)
class Decided:
    position: int
    outcome: Outcome


@dataclass(frozen=True)
class Approver:
    """What the rules need to know about the person deciding."""

    id: uuid.UUID
    roles: frozenset[str]
    department_id: uuid.UUID


def steps_for(steps: Iterable[Step], amount: Decimal) -> tuple[Step, ...]:
    """The steps that apply to this amount, in order."""
    applicable = [step for step in steps if amount > step.above]
    return tuple(sorted(applicable, key=lambda step: step.position))


def state_of(steps: Sequence[Step], decisions: Sequence[Decided], *, cancelled: bool) -> RunState:
    if cancelled:
        return RunState.CANCELLED
    if any(decision.outcome is Outcome.REJECTED for decision in decisions):
        return RunState.REJECTED
    decided = {decision.position for decision in decisions}
    if steps and all(step.position in decided for step in steps):
        return RunState.APPROVED
    return RunState.PENDING


def current_step(steps: Sequence[Step], decisions: Sequence[Decided]) -> Step | None:
    """The first step nobody has decided yet, or None when every step is decided."""
    decided = {decision.position for decision in decisions}
    for step in sorted(steps, key=lambda step: step.position):
        if step.position not in decided:
            return step
    return None


def refusal_for(
    step: Step,
    approver: Approver,
    *,
    started_by: uuid.UUID,
    subject_department_id: uuid.UUID,
    earlier_deciders: frozenset[uuid.UUID],
) -> str | None:
    """Why this person may not decide this step, or None when they may.

    Separation of duties: the person who started the run never decides it, whatever their roles,
    and nobody decides two steps of the same run.
    """
    if approver.id == started_by:
        return "Nobody can decide their own request."
    if approver.id in earlier_deciders:
        return "You already decided an earlier step of this request."
    if step.role not in approver.roles:
        return f"Only a {step.role} can decide this step."
    if step.department_scoped and approver.department_id != subject_department_id:
        return "This request belongs to another department."
    return None
