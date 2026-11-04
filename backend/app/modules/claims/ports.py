"""Ports the claims module needs from other modules.

The spend module owns closed months. The service depends on the port only, so tests can put a fake
in its place.
"""

from typing import Annotated, Protocol

from fastapi import Depends

from app.modules.spend import SpendService, get_spend_service


class ClosedMonths(Protocol):
    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        """The months, out of these YYYY-MM values, that no spend or claim may change in."""
        ...


class SpendClosedMonths:
    """Closed months as the spend module records them."""

    def __init__(self, spend: SpendService) -> None:
        self._spend = spend

    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        closed = {close.year_month for close in await self._spend.closed_months()}
        return months & closed


def get_closed_months(
    spend: Annotated[SpendService, Depends(get_spend_service)],
) -> ClosedMonths:
    return SpendClosedMonths(spend)
