"""Read models of a claim: its summary, its detail and its approval history.

The drafting and review features both return these, so they live at the module root.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.core.approvals import Approver, DecisionView, RunState, RunView
from app.core.users import CurrentPerson
from app.modules.claims.models import Claim, Line
from app.modules.claims.rules import ClaimStatus, status_of


@dataclass(frozen=True)
class ClaimView:
    """A claim as the review feature sees it: what it needs to decide the claim, and no ORM row."""

    id: uuid.UUID
    claimant_id: uuid.UUID
    department_id: uuid.UUID
    title: str
    version: int


def claim_view_of(claim: Claim) -> ClaimView:
    return ClaimView(
        id=claim.id,
        claimant_id=claim.claimant_id,
        department_id=claim.department_id,
        title=claim.title,
        version=claim.version,
    )


@dataclass(frozen=True)
class ClaimSummary:
    id: uuid.UUID
    title: str
    claimant_id: uuid.UUID
    claimant_name: str
    department_id: uuid.UUID
    department_code: str
    status: ClaimStatus
    total: Decimal
    version: int
    created_at: datetime


@dataclass(frozen=True)
class LineView:
    id: uuid.UUID
    category: str
    spent_on: date
    amount: Decimal
    description: str
    receipt_sha256: str


@dataclass(frozen=True)
class StepRecord:
    position: int
    role: str
    decision: DecisionView | None


@dataclass(frozen=True)
class RunRecord:
    """One submission of the claim, with the decision on each of its steps."""

    id: uuid.UUID
    state: RunState
    started_at: datetime
    started_by_name: str
    cancelled_at: datetime | None
    cancel_reason: str | None
    steps: tuple[StepRecord, ...]


@dataclass(frozen=True)
class ClaimDetail:
    summary: ClaimSummary
    lines: tuple[LineView, ...]
    history: tuple[RunRecord, ...]
    can_edit: bool
    can_submit: bool
    can_withdraw: bool
    can_decide: bool


@dataclass(frozen=True)
class SpendRow:
    """Approved spend for one department, month and category."""

    department_id: uuid.UUID
    month: str
    category: str
    amount: Decimal


def approver_of(caller: CurrentPerson) -> Approver:
    return Approver(id=caller.id, roles=caller.roles, department_id=caller.department_id)


def total_of(lines: Sequence[Line], claim_id: uuid.UUID) -> Decimal:
    return sum(
        (row.amount for row in lines if row.claim_id == claim_id and row.voided_at is None),
        Decimal("0.00"),
    )


def summary_of(claim: Claim, latest: RunView | None, total: Decimal) -> ClaimSummary:
    return ClaimSummary(
        id=claim.id,
        title=claim.title,
        claimant_id=claim.claimant_id,
        claimant_name=claim.claimant_name,
        department_id=claim.department_id,
        department_code=claim.department_code,
        status=status_of(None if latest is None else latest.state),
        total=total,
        version=claim.version,
        created_at=claim.created_at,
    )


def record_of(view: RunView) -> RunRecord:
    decisions = {decision.position: decision for decision in view.decisions}
    return RunRecord(
        id=view.id,
        state=view.state,
        started_at=view.started_at,
        started_by_name=view.started_by_name,
        cancelled_at=view.cancelled_at,
        cancel_reason=view.cancel_reason,
        steps=tuple(
            StepRecord(
                position=step.position, role=step.role, decision=decisions.get(step.position)
            )
            for step in view.steps
        ),
    )
