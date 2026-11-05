"""Batch database access: import batches, their staged chunks and the live spans of chunks.

The only code in the batches feature that runs queries against the database.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.batches.lifecycle import OPEN_STATES, BatchState
from app.modules.knowledge.models import (
    BatchFile,
    Chunk,
    ChunkRange,
    ImportBatch,
)
from app.shared.db import get_session


class BatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    def add(self, row: Any) -> None:
        self._session.add(row)

    def add_all(self, rows: Sequence[Chunk]) -> None:
        self._session.add_all(rows)

    async def flush(self) -> None:
        await self._session.flush()

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

    async def batch_chunks(self, batch_id: uuid.UUID) -> list[Chunk]:
        query = (
            select(Chunk)
            .where(Chunk.batch_id == batch_id)
            .order_by(Chunk.batch_file_id, Chunk.position)
        )
        return list((await self._session.scalars(query)).all())

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


async def get_batch_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BatchRepository:
    return BatchRepository(session)
