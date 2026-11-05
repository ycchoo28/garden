"""Request and response shapes for budget lines. Money leaves the API as strings of cents."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from app.modules.budgets.lines.service import LineView
from app.shared.money import format_money
from app.shared.months import YEAR_MONTH_PATTERN

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class BudgetLineOut(BaseModel):
    line_id: uuid.UUID
    version: int
    department_id: uuid.UUID
    department_code: str
    department_name: str
    year_month: str
    category: str
    amount: str
    reason: str | None
    created_at: datetime
    created_by: uuid.UUID
    current: bool

    @classmethod
    def of(cls, view: LineView) -> "BudgetLineOut":
        return cls(
            line_id=view.line_id,
            version=view.version,
            department_id=view.department_id,
            department_code=view.department_code,
            department_name=view.department_name,
            year_month=view.year_month,
            category=view.category,
            amount=format_money(view.amount),
            reason=view.reason,
            created_at=view.created_at,
            created_by=view.created_by,
            current=view.current,
        )


class LineCreateIn(BaseModel):
    department_id: uuid.UUID
    year_month: str = Field(pattern=YEAR_MONTH_PATTERN)
    category: str = Field(max_length=40)
    amount: str = Field(max_length=24)


class LineReplaceIn(BaseModel):
    version: int = Field(ge=1)
    amount: str = Field(max_length=24)
    reason: Reason
