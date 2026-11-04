"""Gateway database access. Only this module and models.py touch the database."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.gateway.models import Usage
from app.shared.db import get_session


class GatewayRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    def add(self, usage: Usage) -> None:
        self._session.add(usage)

    async def requests_since(
        self, *, person_id: uuid.UUID, cli_login_id: uuid.UUID | None, since: datetime
    ) -> int:
        """Requests one CLI login made since a moment. A browser session counts its own requests."""
        statement = select(func.count()).where(
            Usage.person_id == person_id, Usage.occurred_at >= since
        )
        if cli_login_id is None:
            statement = statement.where(Usage.cli_login_id.is_(None))
        else:
            statement = statement.where(Usage.cli_login_id == cli_login_id)
        return int((await self._session.execute(statement)).scalar_one())

    async def totals_since(
        self, *, person_id: uuid.UUID, since: datetime
    ) -> tuple[int, int, int, int]:
        """Requests and prompt, completion and total tokens a person used since a moment."""
        statement = select(
            func.count(),
            func.coalesce(func.sum(Usage.prompt_tokens), 0),
            func.coalesce(func.sum(Usage.completion_tokens), 0),
            func.coalesce(func.sum(Usage.total_tokens), 0),
        ).where(Usage.person_id == person_id, Usage.occurred_at >= since)
        row = (await self._session.execute(statement)).one()
        return int(row[0]), int(row[1]), int(row[2]), int(row[3])


def get_gateway_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> GatewayRepository:
    return GatewayRepository(session)
