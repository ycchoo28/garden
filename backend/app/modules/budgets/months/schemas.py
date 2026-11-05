"""Request and response shapes for month close."""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, StringConstraints

from app.modules.budgets.models import MonthClose

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


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
