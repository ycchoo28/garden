"""Confirmations database access. Only this module and models.py touch the database."""

import uuid
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.confirmations.models import Confirmation
from app.shared.db import get_session


class ConfirmationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def get(self, confirmation_id: uuid.UUID) -> Confirmation | None:
        return await self._session.get(Confirmation, confirmation_id)

    def add(self, confirmation: Confirmation) -> None:
        self._session.add(confirmation)


def get_confirmation_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConfirmationRepository:
    return ConfirmationRepository(session)
