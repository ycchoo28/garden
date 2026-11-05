"""The budgets side of the claims port. The app wires this in place of the claims placeholder.

It uses the months service, which does not depend on claims, so claims never depends on budgets.
"""

from typing import Annotated

from fastapi import Depends

from app.modules.budgets.months.service import MonthsService, get_months_service


class BudgetClosedMonths:
    """Closed months as the budgets module records them."""

    def __init__(self, months: MonthsService) -> None:
        self._months = months

    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        return await self._months.closed_among(months)


def get_closed_months_for_claims(
    months: Annotated[MonthsService, Depends(get_months_service)],
) -> BudgetClosedMonths:
    return BudgetClosedMonths(months)
