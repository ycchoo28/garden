"""HTTP endpoint of the variance report: budget against approved spend for a range of months."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.users import CurrentPerson, require
from app.modules.budgets.variance.schemas import VarianceOut
from app.modules.budgets.variance.service import VarianceService, get_variance_service
from app.shared.months import YEAR_MONTH_PATTERN
from app.shared.operations import op

router = APIRouter()


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
    service: Annotated[VarianceService, Depends(get_variance_service)],
    first_month: Annotated[str, Query(alias="from", pattern=YEAR_MONTH_PATTERN)],
    last_month: Annotated[str, Query(alias="to", pattern=YEAR_MONTH_PATTERN)],
) -> list[VarianceOut]:
    views = await service.variance(person, first_month=first_month, last_month=last_month)
    return [VarianceOut.of(view) for view in views]
