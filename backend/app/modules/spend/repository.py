"""Spend database access. Only this module and models.py touch the database."""

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.spend.models import MonthClose, Statement, StatementRow
from app.shared.db import get_session

# Imports, voids and month closes take this lock first, so two of them never race each other.
LEDGER_LOCK = text("select pg_advisory_xact_lock(hashtext('spend.ledger'))")


class SpendRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def lock_ledger(self) -> None:
        _ = await self._session.execute(LEDGER_LOCK)

    def add(self, row: Statement | MonthClose) -> None:
        self._session.add(row)

    def add_rows(self, rows: Sequence[StatementRow]) -> None:
        self._session.add_all(rows)

    async def existing_refs(self, refs: Sequence[str]) -> dict[str, str]:
        """Saved references in statements that are not voided, mapped to the statement's name."""
        if not refs:
            return {}
        rows = await self._session.execute(
            select(StatementRow.transaction_ref, Statement.source_name)
            .join(Statement, Statement.id == StatementRow.statement_id)
            .where(Statement.voided_at.is_(None), StatementRow.transaction_ref.in_(list(refs)))
        )
        return {reference: name for reference, name in rows.all()}

    async def closed_months(self) -> frozenset[str]:
        return frozenset((await self._session.scalars(select(MonthClose.year_month))).all())

    async def month_close(self, year_month: str) -> MonthClose | None:
        return await self._session.get(MonthClose, year_month)

    async def month_closes(self) -> Sequence[MonthClose]:
        query = select(MonthClose).order_by(MonthClose.year_month.desc())
        return (await self._session.scalars(query)).all()

    async def statement(
        self, statement_id: uuid.UUID, *, for_update: bool = False
    ) -> Statement | None:
        query = select(Statement).where(Statement.id == statement_id)
        if for_update:
            query = query.with_for_update()
        return await self._session.scalar(query)

    async def statements(self) -> Sequence[Statement]:
        query = select(Statement).order_by(Statement.imported_at.desc())
        return (await self._session.scalars(query)).all()

    async def statement_months(self, statement_id: uuid.UUID) -> frozenset[str]:
        query = (
            select(StatementRow.year_month)
            .where(StatementRow.statement_id == statement_id)
            .distinct()
        )
        return frozenset((await self._session.scalars(query)).all())

    async def actual_spend(
        self,
        *,
        first_month: str,
        last_month: str,
        department_ids: frozenset[uuid.UUID] | None,
    ) -> dict[tuple[uuid.UUID, str, str], Decimal]:
        """Spend from statements that are not voided, summed per department, month and category."""
        query = (
            select(
                StatementRow.department_id,
                StatementRow.year_month,
                StatementRow.category,
                func.sum(StatementRow.amount),
            )
            .join(Statement, Statement.id == StatementRow.statement_id)
            .where(
                Statement.voided_at.is_(None),
                StatementRow.year_month >= first_month,
                StatementRow.year_month <= last_month,
            )
            .group_by(StatementRow.department_id, StatementRow.year_month, StatementRow.category)
        )
        if department_ids is not None:
            query = query.where(StatementRow.department_id.in_(list(department_ids)))
        rows = (await self._session.execute(query)).all()
        return {
            (department_id, year_month, category): Decimal(total)
            for department_id, year_month, category, total in rows
        }


def get_repository(session: Annotated[AsyncSession, Depends(get_session)]) -> SpendRepository:
    return SpendRepository(session)
