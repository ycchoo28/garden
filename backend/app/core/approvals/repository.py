"""Approvals database access. Only this module and models.py touch the database."""

import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.approvals.models import Decision, Flow, FlowStep, Run, RunStep
from app.shared.db import get_session


class ApprovalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def flush(self) -> None:
        await self._session.flush()

    def add(self, row: Any) -> None:
        self._session.add(row)

    async def flow(self, code: str) -> Flow | None:
        return await self._session.get(Flow, code)

    async def flow_steps(self, code: str) -> Sequence[FlowStep]:
        query = select(FlowStep).where(FlowStep.flow_code == code).order_by(FlowStep.position)
        return (await self._session.scalars(query)).all()

    async def run(self, run_id: uuid.UUID) -> Run | None:
        return await self._session.get(Run, run_id)

    async def runs(
        self, subject_type: str, subject_ids: Sequence[uuid.UUID] | None
    ) -> Sequence[Run]:
        """Runs of one subject type, newest first, limited to the given subjects if any."""
        query = select(Run).where(Run.subject_type == subject_type)
        if subject_ids is not None:
            query = query.where(Run.subject_id.in_(subject_ids))
        query = query.order_by(Run.started_at.desc(), Run.id.desc())
        return (await self._session.scalars(query)).all()

    async def run_steps(self, run_ids: Sequence[uuid.UUID]) -> Sequence[RunStep]:
        if not run_ids:
            return []
        query = select(RunStep).where(RunStep.run_id.in_(run_ids)).order_by(RunStep.position)
        return (await self._session.scalars(query)).all()

    async def decisions(self, run_ids: Sequence[uuid.UUID]) -> Sequence[Decision]:
        if not run_ids:
            return []
        query = select(Decision).where(Decision.run_id.in_(run_ids)).order_by(Decision.position)
        return (await self._session.scalars(query)).all()


def get_approval_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ApprovalRepository:
    return ApprovalRepository(session)
