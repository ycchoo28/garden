"""Audit database access. Only this module and models.py touch the database."""

from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit.models import AuditEvent
from app.shared.db import get_session


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: AuditEvent) -> None:
        self._session.add(event)

    async def recent(self, limit: int) -> Sequence[AuditEvent]:
        query = select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
        return (await self._session.scalars(query)).all()


def get_audit_repository(session: Annotated[AsyncSession, Depends(get_session)]) -> AuditRepository:
    return AuditRepository(session)
