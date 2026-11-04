"""Request and response shapes for the spend module. Money leaves the API as two-decimal strings."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.modules.spend.models import MonthClose, Statement
from app.modules.spend.statement_csv import Check, Issue
from app.shared.money import format_money

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class RowIssueOut(BaseModel):
    row: int
    field: str
    message: str
    transaction_ref: str | None = None

    @classmethod
    def of(cls, issue: Issue) -> "RowIssueOut":
        return cls(
            row=issue.row,
            field=issue.field,
            message=issue.message,
            transaction_ref=issue.transaction_ref,
        )


class StatementPreviewOut(BaseModel):
    rows_read: int
    total_amount: str
    can_apply: bool
    errors: list[RowIssueOut]
    duplicates: list[RowIssueOut]

    @classmethod
    def of(cls, checked: Check) -> "StatementPreviewOut":
        return cls(
            rows_read=checked.rows_read,
            total_amount=format_money(checked.total_amount),
            can_apply=checked.can_apply,
            errors=[RowIssueOut.of(issue) for issue in checked.errors],
            duplicates=[RowIssueOut.of(issue) for issue in checked.duplicates],
        )


class StatementOut(BaseModel):
    id: uuid.UUID
    source_name: str
    row_count: int
    total_amount: str
    imported_at: datetime
    imported_by: uuid.UUID
    voided: bool
    voided_at: datetime | None
    voided_by: uuid.UUID | None
    void_reason: str | None

    @classmethod
    def of(cls, statement: Statement) -> "StatementOut":
        return cls(
            id=statement.id,
            source_name=statement.source_name,
            row_count=statement.row_count,
            total_amount=format_money(statement.total_amount),
            imported_at=statement.imported_at,
            imported_by=statement.imported_by,
            voided=statement.voided_at is not None,
            voided_at=statement.voided_at,
            voided_by=statement.voided_by,
            void_reason=statement.void_reason,
        )


class VoidIn(BaseModel):
    reason: Reason


class CloseIn(BaseModel):
    reason: Reason


class MonthOut(BaseModel):
    year_month: str
    closed_at: datetime
    closed_by: uuid.UUID
    reason: str

    @classmethod
    def of(cls, close: MonthClose) -> "MonthOut":
        return cls(
            year_month=close.year_month,
            closed_at=close.closed_at,
            closed_by=close.closed_by,
            reason=close.reason,
        )
