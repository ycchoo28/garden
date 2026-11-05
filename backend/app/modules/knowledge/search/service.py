"""Search rules: hybrid search over live chunks, search as of a past moment, preview, and the log.

Endpoints call this, never the database. Access rules apply in SQL, so a result the caller may not
read is never returned.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends

from app.core.users import CurrentPerson
from app.modules.knowledge.batches.service import BatchesService, get_batches_service
from app.modules.knowledge.collections.service import (
    CollectionsService,
    get_collections_service,
)
from app.modules.knowledge.models import Chunk, SearchLog
from app.modules.knowledge.reading.embedding import Embedder, get_embedder
from app.modules.knowledge.reading.text import detect_language, extract_identifiers
from app.modules.knowledge.schemas import (
    KnowledgeSearchHitOut,
    KnowledgeSearchIn,
    KnowledgeSearchLogOut,
    KnowledgeSearchOut,
    PreviewHitOut,
    PreviewIn,
    PreviewOut,
)
from app.modules.knowledge.search.repository import Scope, SearchRepository, get_search_repository
from app.modules.knowledge.search.rrf import rrf_merge
from app.modules.knowledge.search.search import Candidate, assemble
from app.shared.clock import Clock, get_clock
from app.shared.errors import Unprocessable
from app.shared.ids import new_id
from app.shared.request_login import VIA_AGENT, current_request_login

# How many candidates each ranking contributes before fusion. Preview keeps the first few after.
CANDIDATES_PER_RANKING = 50
FUSED_DEPTH = 50


class SearchService:
    def __init__(
        self,
        repository: SearchRepository,
        clock: Clock,
        embedder: Embedder,
        collections: CollectionsService,
        batches: BatchesService,
    ) -> None:
        self._repository = repository
        self._clock = clock
        self._embedder = embedder
        self._collections = collections
        self._batches = batches

    async def search(self, actor: CurrentPerson, body: KnowledgeSearchIn) -> KnowledgeSearchOut:
        """Hybrid search over the chunks live now, or at `as_of`. Access rules apply in SQL."""
        now = self._clock.now()
        as_of = None if body.as_of is None else _as_utc(body.as_of)
        if as_of is not None and as_of > now:
            raise Unprocessable("A search can look back in time, not forward.")
        at = as_of or now
        vector = (await self._embedder.embed([body.query]))[0]
        roles = sorted(actor.roles)
        merged = await self._rank(Scope.live(at), body.query, vector, roles, actor.department_code)
        fused = [chunk_id for chunk_id, _ in merged[:FUSED_DEPTH]]
        scores = dict(merged)
        rows = await self._repository.chunks_by_ids(fused)
        candidates = await self._candidates(fused, rows, scores)
        hits, truncated = assemble(
            candidates,
            query_language=detect_language(body.query),
            limit=body.limit,
            max_tokens=body.max_tokens,
        )
        collection_of = {row.id: row.collection_id for row in rows.values()}
        names = await self._collections.names(list(set(collection_of.values())))
        results: list[KnowledgeSearchHitOut] = []
        for hit in hits:
            collection_id = collection_of[hit.chunk_ids[0]]
            results.append(
                KnowledgeSearchHitOut(
                    chunk_ids=list(hit.chunk_ids),
                    collection_id=collection_id,
                    collection_name=names.get(collection_id, ""),
                    title=hit.title,
                    translation_group=hit.translation_group,
                    language=hit.language,
                    heading_path=hit.heading_path,
                    locator=hit.locator,
                    text=hit.text,
                    token_count=hit.token_count,
                    score=hit.score,
                )
            )
        login = current_request_login.get()
        self._repository.add(
            SearchLog(
                id=new_id(),
                person_id=actor.id,
                person_name=actor.name,
                acting_as_agent=login is not None and login.via == VIA_AGENT,
                query=body.query,
                as_of=as_of,
                chunk_ids=[chunk_id for hit in hits for chunk_id in hit.chunk_ids],
                searched_at=now,
            )
        )
        await self._repository.commit()
        return KnowledgeSearchOut(
            results=results,
            truncated=truncated,
            token_count=sum(hit.token_count for hit in hits),
            as_of=as_of,
        )

    async def preview(
        self, actor: CurrentPerson, batch_id: uuid.UUID, body: PreviewIn
    ) -> PreviewOut:
        await self._batches.require_prepared(batch_id)
        vector = (await self._embedder.embed([body.query]))[0]
        roles = sorted(actor.roles)
        department = actor.department_code
        scope = Scope.staged(batch_id)
        merged = await self._rank(scope, body.query, vector, roles, department)
        fused = [chunk_id for chunk_id, _ in merged[:FUSED_DEPTH]]
        scores = dict(merged)
        rows = await self._repository.chunks_by_ids(fused)
        candidates = await self._candidates(fused, rows, scores)
        hits, truncated = assemble(
            candidates,
            query_language=detect_language(body.query),
            limit=body.limit,
            max_tokens=body.max_tokens,
        )
        return PreviewOut(
            results=[
                PreviewHitOut(
                    chunk_ids=list(hit.chunk_ids),
                    batch_file_id=hit.batch_file_id,
                    title=hit.title,
                    translation_group=hit.translation_group,
                    language=hit.language,
                    heading_path=hit.heading_path,
                    locator=hit.locator,
                    text=hit.text,
                    token_count=hit.token_count,
                    score=hit.score,
                )
                for hit in hits
            ],
            truncated=truncated,
            token_count=sum(hit.token_count for hit in hits),
        )

    async def search_log(self, limit: int) -> list[KnowledgeSearchLogOut]:
        return [
            KnowledgeSearchLogOut(
                id=row.id,
                searched_at=row.searched_at,
                person_id=row.person_id,
                person_name=row.person_name,
                acting_as_agent=row.acting_as_agent,
                query=row.query,
                as_of=row.as_of,
                chunk_ids=list(row.chunk_ids),
            )
            for row in await self._repository.search_logs(limit)
        ]

    async def _rank(
        self,
        scope: Scope,
        query: str,
        vector: list[float],
        roles: list[str],
        department: str,
    ) -> list[tuple[uuid.UUID, float]]:
        rankings = [
            await self._repository.vector_candidates(
                scope, vector, roles, department, CANDIDATES_PER_RANKING
            ),
            await self._repository.keyword_candidates(
                scope, query, roles, department, CANDIDATES_PER_RANKING
            ),
        ]
        identifiers = extract_identifiers(query)
        if identifiers:
            rankings.append(
                await self._repository.identifier_candidates(
                    scope, identifiers, roles, department, CANDIDATES_PER_RANKING
                )
            )
        return rrf_merge([ranking for ranking in rankings if ranking])

    async def _candidates(
        self,
        ids: Sequence[uuid.UUID],
        rows: dict[uuid.UUID, Chunk],
        scores: dict[uuid.UUID, float],
    ) -> list[Candidate]:
        parent_ids = [row.parent_id for row in rows.values() if row.parent_id is not None]
        parents = await self._repository.chunks_by_ids(parent_ids)
        candidates: list[Candidate] = []
        for chunk_id in ids:
            row = rows.get(chunk_id)
            if row is None:
                continue
            unit = parents.get(row.parent_id) if row.parent_id is not None else row
            if unit is None:
                continue
            candidates.append(
                Candidate(
                    chunk_id=row.id,
                    unit_id=unit.id,
                    batch_file_id=row.batch_file_id,
                    translation_group=row.translation_group,
                    title=row.title,
                    language=unit.language,
                    heading_path=unit.heading_path,
                    locator=unit.locator,
                    text=unit.body,
                    token_count=unit.token_count,
                    score=scores.get(chunk_id, 0.0),
                )
            )
        return candidates


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def get_search_service(
    repository: Annotated[SearchRepository, Depends(get_search_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
    collections: Annotated[CollectionsService, Depends(get_collections_service)],
    batches: Annotated[BatchesService, Depends(get_batches_service)],
) -> SearchService:
    return SearchService(repository, clock, embedder, collections, batches)
