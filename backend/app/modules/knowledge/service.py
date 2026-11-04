"""Business rules. Endpoints call this, never the database."""

import uuid
from collections.abc import Sequence
from pathlib import PurePath
from typing import Annotated

from fastapi import Depends

from app.core.audit import AuditService, get_audit_service
from app.core.files import FileService, get_file_service
from app.core.users import CurrentPerson
from app.modules.knowledge.embedding import Embedder, get_embedder
from app.modules.knowledge.jobs import BatchQueue, get_batch_queue
from app.modules.knowledge.lifecycle import BatchEvent, BatchState, InvalidTransition, transition
from app.modules.knowledge.models import BatchFile, Chunk, Collection, CollectionRule, ImportBatch
from app.modules.knowledge.repository import KnowledgeRepository, get_repository
from app.modules.knowledge.rrf import rrf_merge
from app.modules.knowledge.schemas import (
    ASSIGNABLE_ROLES,
    BatchFileOut,
    BatchIn,
    BatchOut,
    CollectionIn,
    CollectionOut,
    CollectionRuleOut,
    PreviewHitOut,
    PreviewIn,
    PreviewOut,
    StatusOut,
)
from app.modules.knowledge.search import Candidate, assemble
from app.modules.knowledge.text import detect_language, extract_identifiers
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, NotFound, Unprocessable
from app.shared.ids import new_id

# How many candidates each ranking contributes before fusion. Preview keeps the first few after.
CANDIDATES_PER_RANKING = 50
FUSED_DEPTH = 50


class KnowledgeService:
    def __init__(
        self,
        repository: KnowledgeRepository,
        audit: AuditService,
        clock: Clock,
        files: FileService,
        queue: BatchQueue,
        embedder: Embedder,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._clock = clock
        self._files = files
        self._queue = queue
        self._embedder = embedder

    async def status(self) -> StatusOut:
        await self._repository.ping()
        return StatusOut(module="knowledge", status="ok", database="ok")

    # Collections

    async def list_collections(self) -> list[CollectionOut]:
        return [_collection_out(row) for row in await self._repository.list_collections()]

    async def get_collection(self, collection_id: uuid.UUID) -> CollectionOut:
        return _collection_out(await self._collection(collection_id))

    async def create_collection(self, actor: CurrentPerson, body: CollectionIn) -> CollectionOut:
        if await self._repository.collection_name_taken(body.name):
            raise Conflict("A collection with this name already exists.")
        rules = _rules(body)
        collection = Collection(
            name=body.name,
            created_by=actor.id,
            created_at=self._clock.now(),
            rules=rules,
        )
        self._repository.add(collection)
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.collection.create",
            target=f"collection:{collection.id}",
            summary=f"Created collection {body.name} with {len(rules)} access rule(s).",
        )
        await self._repository.commit()
        return _collection_out(collection)

    # Batches

    async def create_batch(
        self, actor: CurrentPerson, collection_id: uuid.UUID, body: BatchIn
    ) -> BatchOut:
        collection = await self._collection(collection_id)
        if await self._repository.open_batch_exists(collection.id):
            raise Conflict(
                "This collection already has an open batch. Finish or cancel it first.",
                extensions={"code": "batch_in_progress"},
            )
        batch = ImportBatch(
            id=new_id(),
            collection_id=collection.id,
            mode=body.mode,
            state=BatchState.UPLOADED.value,
            failure_reason=None,
            report={},
            created_by=actor.id,
            created_at=self._clock.now(),
            prepared_at=None,
        )
        self._repository.add(batch)
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.create",
            target=f"batch:{batch.id}",
            summary=f"Opened a {body.mode} batch for {collection.name}.",
        )
        await self._repository.commit()
        return await self._batch_out(batch)

    async def get_batch(self, batch_id: uuid.UUID) -> BatchOut:
        return await self._batch_out(await self._batch(batch_id))

    async def upload_file(
        self,
        actor: CurrentPerson,
        batch_id: uuid.UUID,
        *,
        data: bytes,
        name: str,
        translation_group: str | None,
    ) -> BatchFileOut:
        batch = await self._batch(batch_id)
        _require_state(
            batch, BatchState.UPLOADED, "Files can only be added before a batch is prepared."
        )
        stored = await self._files.upload(data=data, name=name, actor_id=actor.id)
        file_id = new_id()
        clean_name = PurePath(name).name[:255] or "upload"
        row = BatchFile(
            id=file_id,
            batch_id=batch.id,
            sha256=stored.sha256,
            name=clean_name,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            translation_group=(translation_group or str(file_id))[:200],
            parse_status=None,
            parse_error=None,
            title=None,
            language=None,
        )
        self._repository.add(row)
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.upload",
            target=f"batch:{batch.id}",
            summary=f"Added {clean_name} ({stored.size_bytes} bytes) to the batch.",
        )
        await self._repository.commit()
        return _file_out(row)

    async def prepare_batch(self, actor: CurrentPerson, batch_id: uuid.UUID) -> BatchOut:
        batch = await self._batch(batch_id)
        if not await self._batch_has_files(batch.id):
            raise Unprocessable("Add at least one file before preparing the batch.")
        batch.state = _next(batch, BatchEvent.PREPARE).value
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.prepare",
            target=f"batch:{batch.id}",
            summary="Started preparing the batch in the background.",
        )
        await self._repository.commit()
        await self._queue.enqueue_prepare(batch.id)
        return await self._batch_out(batch)

    async def preview(
        self, actor: CurrentPerson, batch_id: uuid.UUID, body: PreviewIn
    ) -> PreviewOut:
        batch = await self._batch(batch_id)
        _require_state(batch, BatchState.PREPARED, "Preview search needs a prepared batch.")
        vector = (await self._embedder.embed([body.query]))[0]
        roles = sorted(actor.roles)
        department = actor.department_code
        rankings = [
            await self._repository.vector_candidates(
                batch.id, vector, roles, department, CANDIDATES_PER_RANKING
            ),
            await self._repository.keyword_candidates(
                batch.id, body.query, roles, department, CANDIDATES_PER_RANKING
            ),
        ]
        identifiers = extract_identifiers(body.query)
        if identifiers:
            rankings.append(
                await self._repository.identifier_candidates(
                    batch.id, identifiers, roles, department, CANDIDATES_PER_RANKING
                )
            )
        merged = rrf_merge([ranking for ranking in rankings if ranking])
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

    # Helpers

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

    async def _collection(self, collection_id: uuid.UUID) -> Collection:
        collection = await self._repository.get_collection(collection_id)
        if collection is None:
            raise NotFound("The collection does not exist.")
        return collection

    async def _batch(self, batch_id: uuid.UUID) -> ImportBatch:
        batch = await self._repository.get_batch(batch_id)
        if batch is None:
            raise NotFound("The batch does not exist.")
        return batch

    async def _batch_has_files(self, batch_id: uuid.UUID) -> bool:
        return bool(await self._repository.batch_files(batch_id))

    async def _batch_out(self, batch: ImportBatch) -> BatchOut:
        files = await self._repository.batch_files(batch.id)
        return BatchOut(
            id=batch.id,
            collection_id=batch.collection_id,
            mode=batch.mode,
            state=batch.state,
            failure_reason=batch.failure_reason,
            report=dict(batch.report),
            created_at=batch.created_at,
            prepared_at=batch.prepared_at,
            files=[_file_out(row) for row in files],
        )


def _next(batch: ImportBatch, event: BatchEvent) -> BatchState:
    try:
        return transition(BatchState(batch.state), event)
    except InvalidTransition as error:
        raise Conflict(str(error)) from error


def _require_state(batch: ImportBatch, state: BatchState, message: str) -> None:
    if batch.state != state:
        raise Conflict(message)


def _rules(body: CollectionIn) -> list[CollectionRule]:
    unknown = sorted({rule.role for rule in body.rules} - ASSIGNABLE_ROLES)
    if unknown:
        raise Unprocessable(f"Unknown role(s): {', '.join(unknown)}.")
    return [
        CollectionRule(
            role=rule.role,
            all_departments=rule.all_departments,
            department_codes=sorted({code.strip().lower() for code in rule.department_codes}),
        )
        for rule in body.rules
    ]


def _collection_out(collection: Collection) -> CollectionOut:
    return CollectionOut(
        id=collection.id,
        name=collection.name,
        created_at=collection.created_at,
        rules=[
            CollectionRuleOut(
                role=rule.role,
                all_departments=rule.all_departments,
                department_codes=list(rule.department_codes),
            )
            for rule in collection.rules
        ],
    )


def _file_out(row: BatchFile) -> BatchFileOut:
    return BatchFileOut(
        id=row.id,
        sha256=row.sha256,
        name=row.name,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        translation_group=row.translation_group,
        parse_status=row.parse_status,
        parse_error=row.parse_error,
        title=row.title,
        language=row.language,
    )


def get_service(
    repository: Annotated[KnowledgeRepository, Depends(get_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    clock: Annotated[Clock, Depends(get_clock)],
    files: Annotated[FileService, Depends(get_file_service)],
    queue: Annotated[BatchQueue, Depends(get_batch_queue)],
    embedder: Annotated[Embedder, Depends(get_embedder)],
) -> KnowledgeService:
    return KnowledgeService(repository, audit, clock, files, queue, embedder)
