"""The variance report: budget minus approved spend, by department, month and category.

It owns no table. Budgets come from the lines feature and approved spend from claims, and both are
read through their services. Managers see only their own department.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated

from fastapi import Depends

from app.core.users import CurrentPerson, DepartmentView, UserService, get_user_service
from app.modules.budgets.lines.rules import department_code, visible_departments
from app.modules.budgets.lines.service import BudgetLinesService, get_lines_service
from app.modules.claims import ClaimsService, get_claims_service
from app.shared.errors import FieldError, Unprocessable
from app.shared.months import is_year_month


@dataclass(frozen=True)
class VarianceView:
    department_id: uuid.UUID
    department_code: str
    department_name: str
    year_month: str
    category: str
    budget: Decimal | None
    actual: Decimal
    variance: Decimal | None


class VarianceService:
    def __init__(
        self,
        lines: BudgetLinesService,
        claims: ClaimsService,
        users: UserService,
    ) -> None:
        self._lines = lines
        self._claims = claims
        self._users = users

    async def _departments(self) -> dict[uuid.UUID, DepartmentView]:
        return {view.id: view for view in await self._users.departments()}

    async def _actual(
        self,
        *,
        first_month: str,
        last_month: str,
        scope: frozenset[uuid.UUID] | None,
    ) -> dict[tuple[uuid.UUID, str, str], Decimal]:
        """Approved claim lines, per department, month and category."""
        actuals: dict[tuple[uuid.UUID, str, str], Decimal] = {}
        for row in await self._claims.approved_spend(from_month=first_month, to_month=last_month):
            if scope is not None and row.department_id not in scope:
                continue
            key = (row.department_id, row.month, row.category)
            actuals[key] = actuals.get(key, Decimal("0.00")) + row.amount
        return actuals

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
        scope = visible_departments(caller)
        budgets = await self._lines.budget_amounts(
            caller, first_month=first_month, last_month=last_month
        )
        actuals = await self._actual(first_month=first_month, last_month=last_month, scope=scope)
        departments = await self._departments()
        keys = sorted(
            set(budgets) | set(actuals),
            key=lambda key: (department_code(departments, key[0]), key[1], key[2]),
        )
        views: list[VarianceView] = []
        for department_id, year_month, category in keys:
            key = (department_id, year_month, category)
            budget = budgets.get(key)
            actual = actuals.get(key, Decimal("0.00"))
            department = departments.get(department_id)
            views.append(
                VarianceView(
                    department_id=department_id,
                    department_code=department_code(departments, department_id),
                    department_name=department.name if department is not None else "Unknown",
                    year_month=year_month,
                    category=category,
                    budget=budget,
                    actual=actual,
                    variance=None if budget is None else budget - actual,
                )
            )
        return views


def get_variance_service(
    lines: Annotated[BudgetLinesService, Depends(get_lines_service)],
    claims: Annotated[ClaimsService, Depends(get_claims_service)],
    users: Annotated[UserService, Depends(get_user_service)],
) -> VarianceService:
    return VarianceService(lines, claims, users)
