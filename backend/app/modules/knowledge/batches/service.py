"""Batch rules: open, fill, prepare, ask for approval, approve, reject, cancel, undo and erase.

Endpoints call this, never the database. Approval is bound to the staged result the approver saw,
and approving a batch makes it live.
"""

import uuid
from collections.abc import Sequence
from pathlib import PurePath
from typing import Annotated, Any

from fastapi import Depends

from app.core.approvals import ApprovalService, Approver, Outcome, get_approval_service
from app.core.audit import AuditService, get_audit_service
from app.core.files import FileService, get_file_service
from app.core.users import CurrentPerson
from app.modules.knowledge.batches.jobs import BatchQueue, get_batch_queue
from app.modules.knowledge.batches.lifecycle import (
    OPEN_STATES,
    BatchEvent,
    BatchState,
    InvalidTransition,
    transition,
)
from app.modules.knowledge.batches.publish import (
    documents_to_close,
    risk_triggers,
    same_rules,
    staged_digest,
)
from app.modules.knowledge.batches.repository import BatchRepository, get_batch_repository
from app.modules.knowledge.collections.service import (
    CollectionsService,
    CollectionView,
    get_collections_service,
)
from app.modules.knowledge.flows import KNOWLEDGE_FLOW_CODE, RUN_AMOUNT, SUBJECT_TYPE
from app.modules.knowledge.models import (
    BatchFile,
    Chunk,
    ChunkRange,
    ImportBatch,
)
from app.modules.knowledge.rules import rule_out, rule_specs
from app.modules.knowledge.schemas import (
    BatchFileOut,
    BatchIn,
    BatchOut,
    KnowledgeBatchSummaryOut,
    KnowledgeEraseIn,
)
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, NotFound, Unprocessable
from app.shared.ids import new_id


class BatchesService:
    def __init__(
        self,
        repository: BatchRepository,
        audit: AuditService,
        clock: Clock,
        files: FileService,
        queue: BatchQueue,
        approvals: ApprovalService,
        collections: CollectionsService,
    ) -> None:
        self._repository = repository
        self._audit = audit
        self._clock = clock
        self._files = files
        self._queue = queue
        self._approvals = approvals
        self._collections = collections

    async def create_batch(
        self, actor: CurrentPerson, collection_id: uuid.UUID, body: BatchIn
    ) -> BatchOut:
        collection = await self._collections.collection(collection_id)
        if await self._repository.open_batch_exists(collection.id):
            raise Conflict(
                "This collection already has an open batch. Finish or cancel it first.",
                extensions={"code": "batch_in_progress"},
            )
        access_rules = None if body.access_rules is None else rule_specs(body.access_rules)
        batch = ImportBatch(
            id=new_id(),
            collection_id=collection.id,
            mode=body.mode,
            state=BatchState.UPLOADED.value,
            failure_reason=None,
            report={},
            access_rules=access_rules,
            previous_access_rules=None,
            risk=[],
            staged_digest=None,
            approval_run_id=None,
            applied_at=None,
            applied_by=None,
            undone_at=None,
            undone_by=None,
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

    async def list_batches(
        self, collection_id: uuid.UUID | None, state: str | None
    ) -> list[KnowledgeBatchSummaryOut]:
        rows = await self._repository.list_batches(collection_id, state)
        names = await self._collections.names(list({row.collection_id for row in rows}))
        return [
            KnowledgeBatchSummaryOut(
                id=row.id,
                collection_id=row.collection_id,
                collection_name=names.get(row.collection_id, ""),
                mode=row.mode,
                state=row.state,
                risk=list(row.risk),
                created_at=row.created_at,
            )
            for row in rows
        ]

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

    async def require_prepared(self, batch_id: uuid.UUID) -> None:
        """Refuse unless the batch is prepared. Preview search reads only prepared batches."""
        batch = await self._batch(batch_id)
        _require_state(batch, BatchState.PREPARED, "Preview search needs a prepared batch.")

    # Approval and apply

    async def submit_batch(self, actor: CurrentPerson, batch_id: uuid.UUID) -> BatchOut:
        """Ask for approval to apply the batch. The staged result is fixed from now on."""
        batch = await self._batch(batch_id)
        _require_state(
            batch, BatchState.PREPARED, "Only a prepared batch can be sent for approval."
        )
        collection = await self._collections.collection(batch.collection_id)
        files = await self._repository.batch_files(batch.id)
        chunks = await self._repository.batch_chunks(batch.id)
        live = await self._repository.live_document_names(collection.id)
        _replaced, removed = documents_to_close(
            live=live, staged={row.name for row in files}, full_set=batch.mode == "full_set"
        )
        rules_changed = batch.access_rules is not None and not same_rules(
            batch.access_rules, collection.rules
        )
        risk = risk_triggers(removed=len(removed), rules_changed=rules_changed)
        run_id = await self._approvals.start(
            flow_code=KNOWLEDGE_FLOW_CODE,
            subject_type=SUBJECT_TYPE,
            subject_id=batch.id,
            subject_department_id=actor.department_id,
            amount=RUN_AMOUNT,
            started_by=actor.id,
            started_by_name=actor.name,
        )
        batch.state = _next(batch, BatchEvent.REQUIRE_APPROVAL).value
        batch.approval_run_id = run_id
        batch.staged_digest = staged_digest(_payload(batch, files, chunks))
        batch.risk = risk
        batch.report = {**batch.report, "removed_documents": sorted(removed)}
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.submit",
            target=f"batch:{batch.id}",
            summary=(
                f"Asked for approval to apply the batch to {collection.name}. "
                f"Risk: {', '.join(risk) or 'none'}."
            ),
        )
        await self._repository.commit()
        return await self._batch_out(batch)

    async def approve_batch(
        self, actor: CurrentPerson, batch_id: uuid.UUID, reason: str | None
    ) -> BatchOut:
        batch = await self._batch(batch_id)
        _require_state(
            batch, BatchState.AWAITING_APPROVAL, "This batch is not waiting for approval."
        )
        collection = await self._collections.collection(batch.collection_id)
        files = await self._repository.batch_files(batch.id)
        chunks = await self._repository.batch_chunks(batch.id)
        # The approval binds to the result the approver saw. A changed result needs a new request.
        if staged_digest(_payload(batch, files, chunks)) != batch.staged_digest:
            raise Conflict(
                "The staged result changed after approval was asked for. "
                "Cancel the request and ask again.",
                extensions={"code": "staged_result_changed"},
            )
        await self._decide(batch, actor, Outcome.APPROVED, reason)
        await self._publish(batch, collection, files, chunks, actor)
        await self._repository.commit()
        return await self._batch_out(batch)

    async def reject_batch(
        self, actor: CurrentPerson, batch_id: uuid.UUID, reason: str | None
    ) -> BatchOut:
        batch = await self._batch(batch_id)
        _require_state(
            batch, BatchState.AWAITING_APPROVAL, "This batch is not waiting for approval."
        )
        await self._decide(batch, actor, Outcome.REJECTED, reason)
        batch.state = _next(batch, BatchEvent.REJECT).value
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.reject",
            target=f"batch:{batch.id}",
            summary=f"Rejected the batch: {(reason or '').strip()}",
        )
        await self._repository.commit()
        return await self._batch_out(batch)

    async def cancel_batch(
        self, actor: CurrentPerson, batch_id: uuid.UUID, reason: str | None
    ) -> BatchOut:
        batch = await self._batch(batch_id)
        if batch.state not in {state.value for state in OPEN_STATES}:
            raise Conflict(f"A batch that is {batch.state} cannot be cancelled.")
        new_state = _next(batch, BatchEvent.CANCEL)
        if batch.state == BatchState.AWAITING_APPROVAL.value:
            if batch.approval_run_id is None:
                raise Conflict("This batch has no approval request.")
            # Only the person who asked for approval may withdraw that request.
            _ = await self._approvals.cancel(
                run_id=batch.approval_run_id, actor_id=actor.id, reason=reason
            )
        batch.state = new_state.value
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.cancel",
            target=f"batch:{batch.id}",
            summary="Cancelled the batch.",
        )
        await self._repository.commit()
        return await self._batch_out(batch)

    async def undo_batch(self, actor: CurrentPerson, batch_id: uuid.UUID) -> BatchOut:
        """Put the live state back to what it was before this batch was applied."""
        batch = await self._batch(batch_id)
        _require_state(batch, BatchState.APPLIED, "Only an applied batch can be undone.")
        later = [
            other
            for other in await self._repository.applied_batches(batch.collection_id)
            if other.id != batch.id
            and other.applied_at is not None
            and batch.applied_at is not None
            and other.applied_at > batch.applied_at
        ]
        if later:
            raise Conflict("A later batch was applied to this collection. Undo that batch first.")
        collection = await self._collections.collection(batch.collection_id)
        now = self._clock.now()
        for span in await self._repository.open_spans_of(batch.id):
            span.live_to = now
        for closed in await self._repository.spans_closed_by(batch.id):
            self._repository.add(
                ChunkRange(
                    id=new_id(),
                    chunk_id=closed.chunk_id,
                    live_from=now,
                    live_to=None,
                    opened_by_batch_id=closed.opened_by_batch_id,
                    closed_by_batch_id=None,
                )
            )
        if batch.previous_access_rules is not None:
            await self._collections.replace_rules(collection.id, batch.previous_access_rules)
        batch.state = _next(batch, BatchEvent.UNDO).value
        batch.undone_at = now
        batch.undone_by = actor.id
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.undo",
            target=f"batch:{batch.id}",
            summary=f"Undid the batch on {collection.name}.",
        )
        await self._repository.commit()
        return await self._batch_out(batch)

    async def erase_document(
        self, actor: CurrentPerson, collection_id: uuid.UUID, body: KnowledgeEraseIn
    ) -> None:
        """Remove a document from every batch of a collection, search and file storage."""
        collection = await self._collections.collection(collection_id)
        rows = await self._repository.batch_files_named(collection.id, body.name)
        if not rows:
            raise NotFound("No document has that name in this collection.")
        hashes = {row.sha256 for row in rows}
        await self._repository.delete_batch_files([row.id for row in rows])
        await self._repository.flush()
        for sha256 in hashes:
            if not await self._repository.sha_in_use(sha256):
                await self._files.erase(sha256)
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.document.erase",
            target=f"collection:{collection.id}",
            summary=(
                f"Erased document {body.name} from {len(rows)} batch(es): {body.reason.strip()}"
            ),
        )
        await self._repository.commit()

    # Helpers

    async def _publish(
        self,
        batch: ImportBatch,
        collection: CollectionView,
        files: Sequence[BatchFile],
        chunks: Sequence[Chunk],
        actor: CurrentPerson,
    ) -> None:
        """Make the batch live: close what it replaces or removes, open its chunks, set rules."""
        now = self._clock.now()
        live = await self._repository.live_document_names(collection.id)
        replaced, removed = documents_to_close(
            live=live, staged={row.name for row in files}, full_set=batch.mode == "full_set"
        )
        await self._repository.close_documents(
            collection.id, sorted(replaced | removed), now, batch.id
        )
        for chunk in chunks:
            self._repository.add(
                ChunkRange(
                    id=new_id(),
                    chunk_id=chunk.id,
                    live_from=now,
                    live_to=None,
                    opened_by_batch_id=batch.id,
                    closed_by_batch_id=None,
                )
            )
        if batch.access_rules is not None and not same_rules(batch.access_rules, collection.rules):
            batch.previous_access_rules = collection.rules
            await self._collections.replace_rules(collection.id, batch.access_rules)
        batch.state = _next(batch, BatchEvent.APPROVE).value
        batch.applied_at = now
        batch.applied_by = actor.id
        batch.report = {
            **batch.report,
            "replaced_documents": sorted(replaced),
            "removed_documents": sorted(removed),
        }
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.batch.apply",
            target=f"batch:{batch.id}",
            summary=(
                f"Applied the batch to {collection.name}: {len(files)} document(s), "
                f"{len(removed)} removed."
            ),
        )

    async def _decide(
        self,
        batch: ImportBatch,
        actor: CurrentPerson,
        outcome: Outcome,
        reason: str | None,
    ) -> None:
        if batch.approval_run_id is None:
            raise Conflict("This batch has no approval request.")
        _ = await self._approvals.decide(
            run_id=batch.approval_run_id,
            approver=Approver(id=actor.id, roles=actor.roles, department_id=actor.department_id),
            approver_name=actor.name,
            outcome=outcome,
            reason=reason,
        )

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
            risk=list(batch.risk),
            access_rules=(
                None
                if batch.access_rules is None
                else [rule_out(rule) for rule in batch.access_rules]
            ),
            created_at=batch.created_at,
            prepared_at=batch.prepared_at,
            applied_at=batch.applied_at,
            undone_at=batch.undone_at,
            files=[_file_out(row) for row in files],
        )


def _payload(
    batch: ImportBatch, files: Sequence[BatchFile], chunks: Sequence[Chunk]
) -> dict[str, Any]:
    """The staged result in a canonical form. Its digest is what an approval is bound to."""
    return {
        "mode": batch.mode,
        "access_rules": batch.access_rules,
        "files": [
            {
                "id": str(row.id),
                "sha256": row.sha256,
                "name": row.name,
                "translation_group": row.translation_group,
                "title": row.title,
                "language": row.language,
            }
            for row in files
        ],
        "chunks": [
            {
                "id": str(row.id),
                "file": str(row.batch_file_id),
                "kind": row.kind,
                "position": row.position,
                "parent": None if row.parent_id is None else str(row.parent_id),
                "body": row.body,
                "prefix": row.context_prefix,
                "identifiers": list(row.identifiers),
                "tokens": row.token_count,
                "embedding": None if row.embedding is None else [float(v) for v in row.embedding],
            }
            for row in chunks
        ],
    }


def _next(batch: ImportBatch, event: BatchEvent) -> BatchState:
    try:
        return transition(BatchState(batch.state), event)
    except InvalidTransition as error:
        raise Conflict(str(error)) from error


def _require_state(batch: ImportBatch, state: BatchState, message: str) -> None:
    if batch.state != state:
        raise Conflict(message)


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


def get_batches_service(
    repository: Annotated[BatchRepository, Depends(get_batch_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    clock: Annotated[Clock, Depends(get_clock)],
    files: Annotated[FileService, Depends(get_file_service)],
    queue: Annotated[BatchQueue, Depends(get_batch_queue)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
    collections: Annotated[CollectionsService, Depends(get_collections_service)],
) -> BatchesService:
    return BatchesService(repository, audit, clock, files, queue, approvals, collections)
