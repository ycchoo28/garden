"""Claims database access. The only code in the claims module that runs queries."""

import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.claims.models import Claim, Line
from app.shared.db import get_session


class ClaimRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def flush(self) -> None:
        await self._session.flush()

    def add(self, row: Any) -> None:
        self._session.add(row)

    async def claim(self, claim_id: uuid.UUID) -> Claim | None:
        """The claim as it is in the database now, even when this session already holds it."""
        return await self._session.get(Claim, claim_id, populate_existing=True)

    async def claims_by_ids(self, claim_ids: Sequence[uuid.UUID]) -> Sequence[Claim]:
        if not claim_ids:
            return []
        query = select(Claim).where(Claim.id.in_(claim_ids)).order_by(Claim.created_at.desc())
        return (await self._session.scalars(query)).all()

    async def all_claims(self) -> Sequence[Claim]:
        return (await self._session.scalars(select(Claim))).all()

    async def visible_claims(
        self, caller_id: uuid.UUID, departments: frozenset[uuid.UUID] | None
    ) -> Sequence[Claim]:
        """The caller's own claims, plus every claim in the given departments (all when None)."""
        condition = Claim.claimant_id == caller_id
        if departments is None:
            query = select(Claim)
        else:
            query = select(Claim).where(or_(condition, Claim.department_id.in_(departments)))
        query = query.order_by(Claim.created_at.desc())
        return (await self._session.scalars(query)).all()

    async def bump(self, claim_id: uuid.UUID, expected_version: int) -> bool:
        """Move the claim on to the next version, but only from the version the caller saw.

        The update takes the row lock, so two writers cannot both succeed from the same version.
        """
        updated = await self._session.scalar(
            update(Claim)
            .where(Claim.id == claim_id, Claim.version == expected_version)
            .values(version=expected_version + 1)
            .returning(Claim.id)
        )
        return updated is not None

    async def line(self, line_id: uuid.UUID) -> Line | None:
        return await self._session.get(Line, line_id, populate_existing=True)

    async def lines_for(self, claim_ids: Sequence[uuid.UUID]) -> Sequence[Line]:
        """Every line, voided ones included. Callers decide what counts."""
        if not claim_ids:
            return []
        query = (
            select(Line).where(Line.claim_id.in_(claim_ids)).order_by(Line.spent_on, Line.added_at)
        )
        return (await self._session.scalars(query)).all()


def get_claim_repository(session: Annotated[AsyncSession, Depends(get_session)]) -> ClaimRepository:
    return ClaimRepository(session)
