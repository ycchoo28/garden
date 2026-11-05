"""HTTP endpoints of budget lines: the categories, the lines of a month, and their history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.core.users import CurrentPerson, require
from app.modules.budgets.lines.schemas import (
    BudgetLineOut,
    LineCreateIn,
    LineReplaceIn,
)
from app.modules.budgets.lines.service import BudgetLinesService, get_lines_service
from app.shared.categories import CATEGORIES
from app.shared.months import YEAR_MONTH_PATTERN
from app.shared.operations import op

router = APIRouter()


@router.get(
    "/categories",
    response_model=list[str],
    **op(
        "budgets.category.list", "List the spend categories that budget lines and claim lines use."
    ),
)
async def list_categories(
    _person: Annotated[CurrentPerson, require("budgets.view")],
) -> list[str]:
    return list(CATEGORIES)


@router.get(
    "/lines",
    response_model=list[BudgetLineOut],
    **op(
        "budgets.line.list",
        "List the current budget lines for one month. Managers see only their own department.",
    ),
)
async def list_lines(
    person: Annotated[CurrentPerson, require("budgets.view")],
    service: Annotated[BudgetLinesService, Depends(get_lines_service)],
    month: Annotated[str, Query(pattern=YEAR_MONTH_PATTERN)],
    department_id: UUID | None = None,
) -> list[BudgetLineOut]:
    views = await service.list_lines(person, year_month=month, department_id=department_id)
    return [BudgetLineOut.of(view) for view in views]


@router.post(
    "/lines",
    status_code=201,
    response_model=BudgetLineOut,
    **op(
        "budgets.line.create",
        "Create the budget for one department, month and category. Finance and admin only.",
    ),
)
async def create_line(
    body: LineCreateIn,
    person: Annotated[CurrentPerson, require("budgets.manage")],
    service: Annotated[BudgetLinesService, Depends(get_lines_service)],
) -> BudgetLineOut:
    view = await service.create_line(
        person,
        department_id=body.department_id,
        year_month=body.year_month,
        category=body.category,
        amount=body.amount,
    )
    return BudgetLineOut.of(view)


@router.put(
    "/lines/{line_id}",
    response_model=BudgetLineOut,
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
    service: Annotated[BudgetLinesService, Depends(get_lines_service)],
) -> BudgetLineOut:
    view = await service.replace_line(
        person,
        line_id,
        version=body.version,
        amount=body.amount,
        reason=body.reason,
    )
    return BudgetLineOut.of(view)


@router.get(
    "/lines/{line_id}/history",
    response_model=list[BudgetLineOut],
    **op(
        "budgets.line.history",
        "List every version of a budget line, oldest first, with the reason for each replacement.",
    ),
)
async def read_history(
    line_id: UUID,
    person: Annotated[CurrentPerson, require("budgets.view")],
    service: Annotated[BudgetLinesService, Depends(get_lines_service)],
) -> list[BudgetLineOut]:
    views = await service.history(person, line_id)
    return [BudgetLineOut.of(view) for view in views]
