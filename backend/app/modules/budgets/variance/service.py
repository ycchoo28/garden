"""The variance report: budget minus approved spend, by department, month and category."""

from typing import Annotated

from fastapi import Depends

from app.core.users import CurrentPerson
from app.modules.budgets.lines.rules import visible_departments
from app.modules.budgets.variance.repository import VarianceRepository, get_variance_repository
from app.modules.budgets.variance.views import VarianceView
from app.shared.errors import FieldError, Unprocessable
from app.shared.months import is_year_month


class VarianceService:
    def __init__(self, repo: VarianceRepository) -> None:
        self._repo = repo

    async def variance(
        self, caller: CurrentPerson, *, first_month: str, last_month: str
    ) -> list[VarianceView]:
        errors: list[FieldError] = []
        if not is_year_month(first_month):
            errors.append(FieldError(field="from", message="Use a month such as 2026-01."))
        if not is_year_month(last_month):
            errors.append(FieldError(field="to", message="Use a month such as 2026-12."))
        if not errors and first_month > last_month:
            errors.append(FieldError(field="from", message="From must not be after to."))
        if errors:
            raise Unprocessable("The month range is not valid.", errors=errors)

        rows = await self._repo.rows(
            first_month=first_month,
            last_month=last_month,
            department_ids=visible_departments(caller),
        )
        return [
            VarianceView(
                department_id=row.department_id,
                department_code=row.department_code,
                department_name=row.department_name,
                year_month=row.year_month,
                category=row.category,
                budget=row.budget,
                actual=row.actual,
                variance=None if row.budget is None else row.budget - row.actual,
            )
            for row in rows
        ]


def get_variance_service(
    repo: Annotated[VarianceRepository, Depends(get_variance_repository)],
) -> VarianceService:
    return VarianceService(repo)
