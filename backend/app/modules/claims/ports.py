"""Ports the claims module needs from modules that do not exist yet.

The spend module will own closed months. Until it exists, the port answers that no month is closed,
and tests put a fake in its place. The service depends on this port only, so swapping in the spend
module changes one dependency and nothing else.
"""

from typing import Protocol


class ClosedMonths(Protocol):
    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        """The months, out of these YYYY-MM values, that no spend or claim may change in."""
        ...


class NoClosedMonths:
    """Stands in until the spend module can close months."""

    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        return frozenset()


def get_closed_months() -> ClosedMonths:
    return NoClosedMonths()
