"""Ports the claims module needs from other modules.

The budgets module owns closed months, and budgets already depends on claims. So claims never
imports budgets. Instead, the app puts the budgets adapter in place of the placeholder below when it
is built (see `PORTS` in composition.py). The service depends on the port only, so tests can put a
fake in its place.
"""

from typing import Protocol


class ClosedMonths(Protocol):
    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        """The months, out of these YYYY-MM values, that no claim line may change in."""
        ...


def get_closed_months() -> ClosedMonths:
    """Placeholder that composition replaces with the budgets adapter."""
    raise RuntimeError(
        "No closed-months provider is wired into the app. See PORTS in composition.py."
    )
