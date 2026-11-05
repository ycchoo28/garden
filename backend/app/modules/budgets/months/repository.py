"""Month close database access. Only the months feature writes the `month_close` table."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.budgets.models import MonthClose
from app.shared.db import get_session

# Closes take this lock first, so two closes of one month never race each other.
MONTHS_LOCK = text("select pg_advisory_xact_lock(hashtext('budgets.months'))")


class MonthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def lock_months(self) -> None:
        _ = await self._session.execute(MONTHS_LOCK)

    def add(self, row: MonthClose) -> None:
        self._session.add(row)

    async def month_close(self, year_month: str) -> MonthClose | None:
        return await self._session.get(MonthClose, year_month)

    async def month_closes(self) -> Sequence[MonthClose]:
        query = select(MonthClose).order_by(MonthClose.year_month.desc())
        return (await self._session.scalars(query)).all()

    async def closed_among(self, months: frozenset[str]) -> frozenset[str]:
        if not months:
            return frozenset()
        query = select(MonthClose.year_month).where(MonthClose.year_month.in_(list(months)))
        return frozenset((await self._session.scalars(query)).all())


def get_months_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MonthRepository:
    return MonthRepository(session)
