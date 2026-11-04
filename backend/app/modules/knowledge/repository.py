"""The only code in the knowledge module that runs queries against the database."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import ColumnElement, Select, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.modules.knowledge.lifecycle import OPEN_STATES, BatchState
from app.modules.knowledge.models import (
    BatchFile,
    Chunk,
    ChunkRange,
    Collection,
    CollectionRule,
    ImportBatch,
    SearchLog,
)
from app.shared.db import get_session


@dataclass(frozen=True)
class Scope:
    """Which chunks a search reads: one batch staged chunks, or the chunks live at a moment."""

    batch_id: uuid.UUID | None
    at: datetime | None

    @classmethod
    def staged(cls, batch_id: uuid.UUID) -> "Scope":
        return cls(batch_id=batch_id, at=None)

    @classmethod
    def live(cls, at: datetime) -> "Scope":
        return cls(batch_id=None, at=at)


class KnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def ping(self) -> None:
        _ = await self._session.execute(text("select 1"))

    async def commit(self) -> None:
        await self._session.commit()

    def add(self, row: Any) -> None:
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

    async def collection_names(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await self._session.execute(
            select(Collection.id, Collection.name).where(Collection.id.in_(ids))
        )
        return {row.id: row.name for row in rows}

    async def open_batch_exists(self, collection_id: uuid.UUID) -> bool:
        query = select(ImportBatch.id).where(
            ImportBatch.collection_id == collection_id,
            ImportBatch.state.in_([state.value for state in OPEN_STATES]),
        )
        return await self._session.scalar(query.limit(1)) is not None

    # Batches and their files

    async def get_batch(self, batch_id: uuid.UUID) -> ImportBatch | None:
        return await self._session.get(ImportBatch, batch_id)

    async def list_batches(
        self, collection_id: uuid.UUID | None, state: str | None
    ) -> Sequence[ImportBatch]:
        query = select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(200)
        if collection_id is not None:
            query = query.where(ImportBatch.collection_id == collection_id)
        if state is not None:
            query = query.where(ImportBatch.state == state)
        return (await self._session.scalars(query)).all()

    async def applied_batches(self, collection_id: uuid.UUID) -> Sequence[ImportBatch]:
        query = select(ImportBatch).where(
            ImportBatch.collection_id == collection_id,
            ImportBatch.state == BatchState.APPLIED.value,
        )
        return (await self._session.scalars(query)).all()

    async def batch_files(self, batch_id: uuid.UUID) -> list[BatchFile]:
        query = select(BatchFile).where(BatchFile.batch_id == batch_id).order_by(BatchFile.name)
        return list((await self._session.scalars(query)).all())

    async def batch_chunks(self, batch_id: uuid.UUID) -> list[Chunk]:
        query = (
            select(Chunk)
            .where(Chunk.batch_id == batch_id)
            .order_by(Chunk.batch_file_id, Chunk.position)
        )
        return list((await self._session.scalars(query)).all())

    async def batch_files_named(self, collection_id: uuid.UUID, name: str) -> Sequence[BatchFile]:
        query = (
            select(BatchFile)
            .join(ImportBatch, ImportBatch.id == BatchFile.batch_id)
            .where(ImportBatch.collection_id == collection_id, BatchFile.name == name)
        )
        return (await self._session.scalars(query)).all()

    async def delete_batch_files(self, ids: Sequence[uuid.UUID]) -> None:
        """Delete the files; their chunks and chunk spans go with them through the foreign keys."""
        if ids:
            _ = await self._session.execute(delete(BatchFile).where(BatchFile.id.in_(ids)))

    async def sha_in_use(self, sha256: str) -> bool:
        found = await self._session.scalar(
            select(BatchFile.id).where(BatchFile.sha256 == sha256).limit(1)
        )
        return found is not None

    # Live spans

    async def live_document_names(self, collection_id: uuid.UUID) -> set[str]:
        query = (
            select(BatchFile.name)
            .select_from(ChunkRange)
            .join(Chunk, Chunk.id == ChunkRange.chunk_id)
            .join(BatchFile, BatchFile.id == Chunk.batch_file_id)
            .where(Chunk.collection_id == collection_id, ChunkRange.live_to.is_(None))
            .distinct()
        )
        return set((await self._session.scalars(query)).all())

    async def close_documents(
        self, collection_id: uuid.UUID, names: Sequence[str], at: datetime, batch_id: uuid.UUID
    ) -> None:
        """End the live spans of every chunk of the named documents, recording the closing batch."""
        if not names:
            return
        chunk_ids = (
            select(Chunk.id)
            .join(BatchFile, BatchFile.id == Chunk.batch_file_id)
            .where(Chunk.collection_id == collection_id, BatchFile.name.in_(names))
        )
        _ = await self._session.execute(
            update(ChunkRange)
            .where(ChunkRange.live_to.is_(None), ChunkRange.chunk_id.in_(chunk_ids))
            .values(live_to=at, closed_by_batch_id=batch_id)
            .execution_options(synchronize_session="fetch")
        )

    async def open_spans_of(self, batch_id: uuid.UUID) -> Sequence[ChunkRange]:
        query = select(ChunkRange).where(
            ChunkRange.opened_by_batch_id == batch_id, ChunkRange.live_to.is_(None)
        )
        return (await self._session.scalars(query)).all()

    async def spans_closed_by(self, batch_id: uuid.UUID) -> Sequence[ChunkRange]:
        query = select(ChunkRange).where(ChunkRange.closed_by_batch_id == batch_id)
        return (await self._session.scalars(query)).all()

    # Search over one scope. Every query applies the access rules in SQL.

    async def vector_candidates(
        self,
        scope: Scope,
        vector: list[float],
        roles: list[str],
        department_code: str,
        limit: int,
    ) -> list[uuid.UUID]:
        query = (
            _searchable(scope, roles, department_code)
            .where(Chunk.embedding.is_not(None))
            .order_by(Chunk.embedding.cosine_distance(vector))
            .limit(limit)
        )
        return list(await self._session.scalars(query))

    async def keyword_candidates(
        self,
        scope: Scope,
        query_text: str,
        roles: list[str],
        department_code: str,
        limit: int,
    ) -> list[uuid.UUID]:
        ts_query = func.websearch_to_tsquery("simple", query_text)
        query = (
            _searchable(scope, roles, department_code)
            .where(Chunk.keyword.op("@@")(ts_query))
            .order_by(func.ts_rank(Chunk.keyword, ts_query).desc(), Chunk.position)
            .limit(limit)
        )
        return list(await self._session.scalars(query))

    async def identifier_candidates(
        self,
        scope: Scope,
        identifiers: list[str],
        roles: list[str],
        department_code: str,
        limit: int,
    ) -> list[uuid.UUID]:
        query = (
            _searchable(scope, roles, department_code)
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

    # The search log

    async def search_logs(self, limit: int) -> Sequence[SearchLog]:
        query = select(SearchLog).order_by(SearchLog.searched_at.desc(), SearchLog.id).limit(limit)
        return (await self._session.scalars(query)).all()


def _readable_collections(roles: list[str], department_code: str) -> Select[uuid.UUID]:
    """Collections with a rule that matches the caller's role and department. Fails closed."""
    return select(CollectionRule.collection_id).where(
        CollectionRule.role.in_(roles),
        or_(
            CollectionRule.all_departments.is_(True),
            CollectionRule.department_codes.contains([department_code]),
        ),
    )


def _scope_clause(scope: Scope) -> ColumnElement[bool]:
    if scope.batch_id is not None:
        return Chunk.batch_id == scope.batch_id
    if scope.at is None:
        raise ValueError("A search scope names a batch or a moment.")
    at = scope.at
    return Chunk.id.in_(
        select(ChunkRange.chunk_id).where(
            ChunkRange.live_from <= at,
            or_(ChunkRange.live_to.is_(None), ChunkRange.live_to > at),
        )
    )


def _searchable(scope: Scope, roles: list[str], department_code: str) -> Select[uuid.UUID]:
    return select(Chunk.id).where(
        _scope_clause(scope),
        Chunk.kind.in_(["single", "child"]),
        Chunk.collection_id.in_(_readable_collections(roles, department_code)),
    )


async def get_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> KnowledgeRepository:
    return KnowledgeRepository(session)
