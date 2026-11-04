"""Files database access. Only this module and models.py touch the database."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.files.models import StoredFile
from app.shared.db import get_session


class FileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    async def get(self, sha256: str) -> StoredFile | None:
        return await self._session.get(StoredFile, sha256)

    def add(self, stored: StoredFile) -> None:
        self._session.add(stored)

    async def remove(self, stored: StoredFile) -> None:
        await self._session.delete(stored)


def get_file_repository(session: Annotated[AsyncSession, Depends(get_session)]) -> FileRepository:
    return FileRepository(session)
