"""Request and response shapes for the claims routes. Money goes out as a two-decimal string."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.core.approvals import Outcome
from app.modules.claims.service import ClaimDetail, ClaimSummary, DecisionView, RunRecord

Money = Annotated[Decimal, Field(gt=Decimal("0"), max_digits=14, decimal_places=2)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CreateClaimIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class VersionIn(BaseModel):
    expected_version: int = Field(ge=1)


class AddLineIn(VersionIn):
    category: str = Field(min_length=1, max_length=40)
    spent_on: date
    amount: Money
    description: str = Field(min_length=1, max_length=300)
    receipt_sha256: Sha256


class VoidLineIn(VersionIn):
    reason: str | None = Field(default=None, max_length=300)


class DecisionIn(VersionIn):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


class DecisionOut(BaseModel):
    outcome: str
    decided_by_name: str
    decided_at: datetime
    reason: str | None

    @classmethod
    def of(cls, view: DecisionView) -> "DecisionOut":
        return cls(
            outcome=view.outcome.value,
            decided_by_name=view.decided_by_name,
            decided_at=view.decided_at,
            reason=view.reason,
        )


class StepOut(BaseModel):
    position: int
    role: str
    decision: DecisionOut | None


class RunOut(BaseModel):
    id: uuid.UUID
    state: str
    started_at: datetime
    started_by_name: str
    cancelled_at: datetime | None
    cancel_reason: str | None
    steps: list[StepOut]

    @classmethod
    def of(cls, record: RunRecord) -> "RunOut":
        return cls(
            id=record.id,
            state=record.state.value,
            started_at=record.started_at,
            started_by_name=record.started_by_name,
            cancelled_at=record.cancelled_at,
            cancel_reason=record.cancel_reason,
            steps=[
                StepOut(
                    position=step.position,
                    role=step.role,
                    decision=None if step.decision is None else DecisionOut.of(step.decision),
                )
                for step in record.steps
            ],
        )


class ClaimSummaryOut(BaseModel):
    id: uuid.UUID
    title: str
    claimant_id: uuid.UUID
    claimant_name: str
    department_id: uuid.UUID
    department_code: str
    status: str
    total: Decimal
    version: int
    created_at: datetime

    @classmethod
    def of(cls, summary: ClaimSummary) -> "ClaimSummaryOut":
        return cls(
            id=summary.id,
            title=summary.title,
            claimant_id=summary.claimant_id,
            claimant_name=summary.claimant_name,
            department_id=summary.department_id,
            department_code=summary.department_code,
            status=summary.status.value,
            total=summary.total,
            version=summary.version,
            created_at=summary.created_at,
        )


class LineOut(BaseModel):
    id: uuid.UUID
    category: str
    spent_on: date
    amount: Decimal
    description: str
    receipt_sha256: str
    receipt_url: str


class ActionsOut(BaseModel):
    can_edit: bool
    can_submit: bool
    can_withdraw: bool
    can_decide: bool


class ClaimDetailOut(BaseModel):
    claim: ClaimSummaryOut
    lines: list[LineOut]
    history: list[RunOut]
    actions: ActionsOut

    @classmethod
    def of(cls, detail: ClaimDetail) -> "ClaimDetailOut":
        return cls(
            claim=ClaimSummaryOut.of(detail.summary),
            lines=[
                LineOut(
                    id=line.id,
                    category=line.category,
                    spent_on=line.spent_on,
                    amount=line.amount,
                    description=line.description,
                    receipt_sha256=line.receipt_sha256,
                    receipt_url=f"/api/files/{line.receipt_sha256}",
                )
                for line in detail.lines
            ],
            history=[RunOut.of(record) for record in detail.history],
            actions=ActionsOut(
                can_edit=detail.can_edit,
                can_submit=detail.can_submit,
                can_withdraw=detail.can_withdraw,
                can_decide=detail.can_decide,
            ),
        )


def outcome_of(decision: Literal["approve", "reject"]) -> Outcome:
    return Outcome.APPROVED if decision == "approve" else Outcome.REJECTED
