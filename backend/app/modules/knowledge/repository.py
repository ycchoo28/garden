"""The only code in the knowledge module that runs queries against the database."""

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Select, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.knowledge.lifecycle import OPEN_STATES
from app.modules.knowledge.models import (
    BatchFile,
    Chunk,
    Collection,
    CollectionRule,
    ImportBatch,
)
from app.shared.db import get_session


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ping(self) -> None:
        _ = await self._session.execute(text("select 1"))

    async def commit(self) -> None:
        await self._session.commit()

    def add(self, row: Collection | CollectionRule | ImportBatch | BatchFile | Chunk) -> None:
        self._session.add(row)

    def add_all(self, rows: Sequence[Chunk]) -> None:
        self._session.add_all(rows)

    async def flush(self) -> None:
        await self._session.flush()

    # Collections

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

    async def open_batch_exists(self, collection_id: uuid.UUID) -> bool:
        query = select(ImportBatch.id).where(
            ImportBatch.collection_id == collection_id,
            ImportBatch.state.in_([state.value for state in OPEN_STATES]),
        )
        return await self._session.scalar(query.limit(1)) is not None

    # Batches and their files

    async def get_batch(self, batch_id: uuid.UUID) -> ImportBatch | None:
        return await self._session.get(ImportBatch, batch_id)

    async def batch_files(self, batch_id: uuid.UUID) -> list[BatchFile]:
        query = select(BatchFile).where(BatchFile.batch_id == batch_id).order_by(BatchFile.name)
        return list((await self._session.scalars(query)).all())

    # Search over the staged chunks of one batch. Every query applies the access rules in SQL.

    async def vector_candidates(
        self,
        batch_id: uuid.UUID,
        vector: list[float],
        roles: list[str],
        department_code: str,
        limit: int,
    ) -> list[uuid.UUID]:
        query = (
            _searchable(batch_id, roles, department_code)
            .where(Chunk.embedding.is_not(None))
            .order_by(Chunk.embedding.cosine_distance(vector))
            .limit(limit)
        )
        return list(await self._session.scalars(query))

    async def keyword_candidates(
        self,
        batch_id: uuid.UUID,
        query_text: str,
        roles: list[str],
        department_code: str,
        limit: int,
    ) -> list[uuid.UUID]:
        ts_query = func.websearch_to_tsquery("simple", query_text)
        query = (
            _searchable(batch_id, roles, department_code)
            .where(Chunk.keyword.op("@@")(ts_query))
            .order_by(func.ts_rank(Chunk.keyword, ts_query).desc(), Chunk.position)
            .limit(limit)
        )
        return list(await self._session.scalars(query))

    async def identifier_candidates(
        self,
        batch_id: uuid.UUID,
        identifiers: list[str],
        roles: list[str],
        department_code: str,
        limit: int,
    ) -> list[uuid.UUID]:
        query = (
            _searchable(batch_id, roles, department_code)
            .where(Chunk.identifiers.overlap(identifiers))
            .order_by(Chunk.batch_file_id, Chunk.position)
            .limit(limit)
        )
        return list(await self._session.scalars(query))

    async def chunks_by_ids(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, Chunk]:
        if not ids:
            return {}
        rows = (await self._session.scalars(select(Chunk).where(Chunk.id.in_(ids)))).all()
        return {row.id: row for row in rows}


def _readable_collections(roles: list[str], department_code: str) -> Select[uuid.UUID]:
    """Collections with a rule that matches the caller's role and department. Fails closed."""
    return select(CollectionRule.collection_id).where(
        CollectionRule.role.in_(roles),
        or_(
            CollectionRule.all_departments.is_(True),
            CollectionRule.department_codes.contains([department_code]),
        ),
    )


def _searchable(batch_id: uuid.UUID, roles: list[str], department_code: str) -> Select[uuid.UUID]:
    return select(Chunk.id).where(
        Chunk.batch_id == batch_id,
        Chunk.kind.in_(["single", "child"]),
        Chunk.collection_id.in_(_readable_collections(roles, department_code)),
    )


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeRepository:
    return KnowledgeRepository(session)
