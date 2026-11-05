"""HTTP endpoints of month close: the closed months, and the close an agent must confirm."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.core.agent import confirm_agent_action
from app.core.users import CurrentPerson, require
from app.modules.budgets.months.schemas import CloseIn, MonthOut
from app.modules.budgets.months.service import MonthsService, get_months_service
from app.shared.months import YEAR_MONTH_PATTERN
from app.shared.operations import op

router = APIRouter()


@router.get(
    "/months",
    response_model=list[MonthOut],
    **op("budgets.month.list", "List closed months, newest first. Finance and admin only."),
)
async def list_closed_months(
    _person: Annotated[CurrentPerson, require("budgets.month.close")],
    service: Annotated[MonthsService, Depends(get_months_service)],
) -> list[MonthOut]:
    return [MonthOut.of(close) for close in await service.closed_months()]


@router.post(
    "/months/{year_month}/close",
    status_code=201,
    response_model=MonthOut,
    dependencies=[confirm_agent_action("budgets.month.close")],
    **op(
        "budgets.month.close",
        "Close a month with a reason. Afterwards no claim line for that month can be added or "
        "changed, and a closed month never reopens. An agent's request waits for the person's "
        "confirmation.",
        confirm=True,
    ),
)
async def close_month(
    year_month: Annotated[str, Path(pattern=YEAR_MONTH_PATTERN)],
    body: CloseIn,
    person: Annotated[CurrentPerson, require("budgets.month.close")],
    service: Annotated[MonthsService, Depends(get_months_service)],
) -> MonthOut:
    close = await service.close_month(person, year_month=year_month, reason=body.reason)
    return MonthOut.of(close)
