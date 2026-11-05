"""Collection database access: a collection and its access rules, and the module health check."""

import uuid
from collections.abc import Mapping, Sequence
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.knowledge.models import Collection, CollectionRule
from app.shared.db import get_session


class CollectionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ping(self) -> None:
        _ = await self._session.execute(text("select 1"))

    async def commit(self) -> None:
        await self._session.commit()

    def add(self, row: Any) -> None:
        self._session.add(row)

    async def collection_name_taken(self, name: str) -> bool:
        found = await self._session.scalar(select(Collection.id).where(Collection.name == name))
        return found is not None

    async def list_collections(self) -> list[Collection]:
        query = select(Collection).order_by(Collection.name).options(selectinload(Collection.rules))
        return list((await self._session.scalars(query)).all())

    async def get_collection(self, collection_id: uuid.UUID) -> Collection | None:
        query = (
            select(Collection)
            .where(Collection.id == collection_id)
            .options(selectinload(Collection.rules))
        )
        return await self._session.scalar(query)

    async def replace_rules(
        self, collection_id: uuid.UUID, specs: Sequence[Mapping[str, Any]]
    ) -> bool:
        """Set the collection's rules to these specs. False when the collection does not exist."""
        collection = await self.get_collection(collection_id)
        if collection is None:
            return False
        collection.rules = [CollectionRule(**spec) for spec in specs]
        return True

    async def collection_names(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await self._session.execute(
            select(Collection.id, Collection.name).where(Collection.id.in_(ids))
        )
        return {row.id: row.name for row in rows}


async def get_collection_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CollectionRepository:
    return CollectionRepository(session)
