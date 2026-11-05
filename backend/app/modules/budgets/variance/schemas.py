"""Request and response shapes for the variance report. Money leaves the API as strings of cents."""

import uuid

from pydantic import BaseModel

from app.modules.budgets.variance.service import VarianceView
from app.shared.money import format_money


class VarianceOut(BaseModel):
    department_id: uuid.UUID
    department_code: str
    department_name: str
    year_month: str
    category: str
    budget: str | None
    actual: str
    variance: str | None

    @classmethod
    def of(cls, view: VarianceView) -> "VarianceOut":
        return cls(
            department_id=view.department_id,
            department_code=view.department_code,
            department_name=view.department_name,
            year_month=view.year_month,
            category=view.category,
            budget=None if view.budget is None else format_money(view.budget),
            actual=format_money(view.actual),
            variance=None if view.variance is None else format_money(view.variance),
        )
