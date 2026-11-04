"""HTTP endpoints of the budgets module: budget lines, their history and the variance report."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.users import CurrentPerson, require
from app.modules.budgets.schemas import LineCreateIn, LineOut, LineReplaceIn, VarianceOut
from app.modules.budgets.service import BudgetService, get_budget_service
from app.modules.spend import CATEGORIES, YEAR_MONTH_PATTERN
from app.shared.operations import op

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


@router.get(
    "/categories",
    response_model=list[str],
    **op("budgets.category.list", "List the spend categories that budget lines and card rows use."),
)
async def list_categories(
    _person: Annotated[CurrentPerson, require("budgets.view")],
) -> list[str]:
    return list(CATEGORIES)


@router.get(
    "/lines",
    response_model=list[LineOut],
    **op(
        "budgets.line.list",
        "List the current budget lines for one month. Managers see only their own department.",
    ),
)
async def list_lines(
    person: Annotated[CurrentPerson, require("budgets.view")],
    service: Annotated[BudgetService, Depends(get_budget_service)],
    month: Annotated[str, Query(pattern=YEAR_MONTH_PATTERN)],
    department_id: UUID | None = None,
) -> list[LineOut]:
    views = await service.list_lines(person, year_month=month, department_id=department_id)
    return [LineOut.of(view) for view in views]


@router.post(
    "/lines",
    status_code=201,
    response_model=LineOut,
    **op(
        "budgets.line.create",
        "Create the budget for one department, month and category. Finance and admin only.",
    ),
)
async def create_line(
    body: LineCreateIn,
    person: Annotated[CurrentPerson, require("budgets.manage")],
    service: Annotated[BudgetService, Depends(get_budget_service)],
) -> LineOut:
    view = await service.create_line(
        person,
        department_id=body.department_id,
        year_month=body.year_month,
        category=body.category,
        amount=body.amount,
    )
    return LineOut.of(view)


@router.put(
    "/lines/{line_id}",
    response_model=LineOut,
    **op(
        "budgets.line.replace",
        "Replace the amount of a budget line with a reason. The old version stays in the line's "
        "history. Send the version you read; a stale version returns 409. Finance and admin only.",
    ),
)
async def replace_line(
    line_id: UUID,
    body: LineReplaceIn,
    person: Annotated[CurrentPerson, require("budgets.manage")],
    service: Annotated[BudgetService, Depends(get_budget_service)],
) -> LineOut:
    view = await service.replace_line(
        person,
        line_id,
        version=body.version,
        amount=body.amount,
        reason=body.reason,
    )
    return LineOut.of(view)


@router.get(
    "/lines/{line_id}/history",
    response_model=list[LineOut],
    **op(
        "budgets.line.history",
        "List every version of a budget line, oldest first, with the reason for each replacement.",
    ),
)
async def read_history(
    line_id: UUID,
    person: Annotated[CurrentPerson, require("budgets.view")],
    service: Annotated[BudgetService, Depends(get_budget_service)],
) -> list[LineOut]:
    views = await service.history(person, line_id)
    return [LineOut.of(view) for view in views]


@router.get(
    "/variance",
    response_model=list[VarianceOut],
    **op(
        "budgets.variance.read",
        "Compare budget with actual spend by department, month and category for a range of "
        "months. Managers see only their own department.",
    ),
)
async def read_variance(
    person: Annotated[CurrentPerson, require("budgets.view")],
    service: Annotated[BudgetService, Depends(get_budget_service)],
    first_month: Annotated[str, Query(alias="from", pattern=YEAR_MONTH_PATTERN)],
    last_month: Annotated[str, Query(alias="to", pattern=YEAR_MONTH_PATTERN)],
) -> list[VarianceOut]:
    views = await service.variance(person, first_month=first_month, last_month=last_month)
    return [VarianceOut.of(view) for view in views]
