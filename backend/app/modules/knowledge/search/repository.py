"""Search database access: the ranking queries, the chunks they return, and the search log.

This is the one repository that reads across tables. The ranking SQL reads `collection_rule` to
apply access rules, and `collection_rule` belongs to the collections feature. The rule is enforced
in SQL, so a search never returns a chunk the caller may not read.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.knowledge.models import Chunk, ChunkRange, CollectionRule, SearchLog
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


class SearchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def commit(self) -> None:
        await self._session.commit()

    def add(self, row: Any) -> None:
        self._session.add(row)

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


async def get_search_repository(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SearchRepository:
    return SearchRepository(session)
