"""Month close rules: close a month with a reason, list the closed months, and say which are closed.

Endpoints call this, never the database. A closed month never reopens. Claims asks through the port
in ports.py, so this service never depends on claims.
"""

from typing import Annotated

from fastapi import Depends

from app.core.audit import AuditService, get_audit_service
from app.core.users import CurrentPerson
from app.modules.budgets.models import MonthClose
from app.modules.budgets.months.repository import MonthRepository, get_months_repository
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict


class MonthsService:
    def __init__(self, repo: MonthRepository, audit: AuditService, clock: Clock) -> None:
        self._repo = repo
        self._audit = audit
        self._clock = clock

    async def closed_months(self) -> list[MonthClose]:
        return list(await self._repo.month_closes())

    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        """The months, out of these YYYY-MM values, that are closed."""
        return await self._repo.closed_among(months)

    async def close_month(
        self, caller: CurrentPerson, *, year_month: str, reason: str
    ) -> MonthClose:
        """Close a month. Its claim lines can no longer change, and a closed month never reopens."""
        await self._repo.lock_months()
        if await self._repo.month_close(year_month) is not None:
            raise Conflict(f"{year_month} is already closed.")
        close = MonthClose(
            year_month=year_month,
            closed_at=self._clock.now(),
            closed_by=caller.id,
            reason=reason,
        )
        self._repo.add(close)
        self._audit.record(
            actor_id=caller.id,
            operation="budgets.month.close",
            target=f"month:{year_month}",
            summary=f"Closed {year_month}: {reason}",
        )
        await self._repo.commit()
        return close


def get_months_service(
    repo: Annotated[MonthRepository, Depends(get_months_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> MonthsService:
    return MonthsService(repo, audit, clock)
