"""Budget line database access. Only the lines feature writes the `line` table."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.budgets.models import BudgetLine
from app.shared.db import get_session

# Creates and replacements take this lock first, so two writers never race on one budget key.
LINES_LOCK = text("select pg_advisory_xact_lock(hashtext('budgets.lines'))")


class BudgetLineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def flush(self) -> None:
        await self._session.flush()

    async def lock_lines(self) -> None:
        _ = await self._session.execute(LINES_LOCK)

    def add(self, row: BudgetLine) -> None:
        self._session.add(row)

    async def current_key_exists(
        self, department_id: uuid.UUID, year_month: str, category: str
    ) -> bool:
        query = select(BudgetLine.id).where(
            BudgetLine.department_id == department_id,
            BudgetLine.year_month == year_month,
            BudgetLine.category == category,
            BudgetLine.superseded_at.is_(None),
        )
        return (await self._session.scalar(query)) is not None

    async def current_for_update(self, line_id: uuid.UUID) -> BudgetLine | None:
        query = (
            select(BudgetLine)
            .where(BudgetLine.line_id == line_id, BudgetLine.superseded_at.is_(None))
            .with_for_update()
        )
        return await self._session.scalar(query)

    async def current_lines(
        self,
        *,
        department_ids: frozenset[uuid.UUID] | None,
        first_month: str,
        last_month: str,
    ) -> Sequence[BudgetLine]:
        query = (
            select(BudgetLine)
            .where(
                BudgetLine.superseded_at.is_(None),
                BudgetLine.year_month >= first_month,
                BudgetLine.year_month <= last_month,
            )
            .order_by(BudgetLine.year_month, BudgetLine.category)
        )
        if department_ids is not None:
            query = query.where(BudgetLine.department_id.in_(list(department_ids)))
        return (await self._session.scalars(query)).all()

    async def history(self, line_id: uuid.UUID) -> Sequence[BudgetLine]:
        query = select(BudgetLine).where(BudgetLine.line_id == line_id).order_by(BudgetLine.version)
        return (await self._session.scalars(query)).all()


def get_lines_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BudgetLineRepository:
    return BudgetLineRepository(session)
