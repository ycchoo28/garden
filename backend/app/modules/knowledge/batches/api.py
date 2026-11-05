"""HTTP endpoints for import batches: open, upload, prepare, ask for approval, decide, cancel, undo.

Erasing a document from a collection is here too.
Approve and reject are person-only. Undo is an agent tool that waits for the person's confirmation.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile

from app.config import Settings, get_settings
from app.core.agent import confirm_agent_action
from app.core.users import CurrentPerson, require
from app.modules.knowledge.batches.service import BatchesService, get_batches_service
from app.modules.knowledge.schemas import (
    BatchFileOut,
    BatchIn,
    BatchOut,
    KnowledgeBatchSummaryOut,
    KnowledgeEraseIn,
    KnowledgeReasonIn,
)
from app.shared.operations import op

UNDO_OPERATION = "knowledge.batch.undo"

router = APIRouter()


@router.post(
    "/collections/{collection_id}/batches",
    response_model=BatchOut,
    status_code=201,
    **op(
        "knowledge.batch.create",
        "Open an import batch for a collection. One may be open at a time.",
    ),
)
async def create_batch(
    collection_id: uuid.UUID,
    body: BatchIn,
    person: Annotated[CurrentPerson, require("knowledge.batch.prepare")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.create_batch(person, collection_id, body)


@router.get(
    "/batches",
    response_model=list[KnowledgeBatchSummaryOut],
    **op(
        "knowledge.batch.list",
        "List import batches, newest first, optionally for one collection or one state.",
    ),
)
async def list_batches(
    _person: Annotated[CurrentPerson, require("knowledge.collection.view")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
    collection_id: uuid.UUID | None = None,
    state: str | None = Query(default=None, max_length=30),
) -> list[KnowledgeBatchSummaryOut]:
    return await service.list_batches(collection_id, state)


@router.get(
    "/batches/{batch_id}",
    response_model=BatchOut,
    **op("knowledge.batch.read", "Read an import batch, its files and its preparation report."),
)
async def read_batch(
    batch_id: uuid.UUID,
    _person: Annotated[CurrentPerson, require("knowledge.batch.prepare")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.get_batch(batch_id)


@router.post(
    "/batches/{batch_id}/files",
    response_model=BatchFileOut,
    status_code=201,
    **op(
        "knowledge.batch.upload",
        "Add one Markdown, text or PDF file to a batch that is still uploading.",
    ),
)
async def upload_file(
    batch_id: uuid.UUID,
    file: UploadFile,
    person: Annotated[CurrentPerson, require("knowledge.batch.prepare")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    translation_group: Annotated[str | None, Form(max_length=200)] = None,
) -> BatchFileOut:
    # Read one byte past the limit, so an oversized upload is told apart from a limit-sized one.
    data = await file.read(settings.files_max_bytes + 1)
    return await service.upload_file(
        person,
        batch_id,
        data=data,
        name=file.filename or "upload",
        translation_group=translation_group,
    )


@router.post(
    "/batches/{batch_id}/prepare",
    response_model=BatchOut,
    **op(
        "knowledge.batch.prepare",
        "Start preparing a batch in the background: parse, chunk and embed its files.",
    ),
)
async def prepare_batch(
    batch_id: uuid.UUID,
    person: Annotated[CurrentPerson, require("knowledge.batch.prepare")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.prepare_batch(person, batch_id)


@router.post(
    "/batches/{batch_id}/submit",
    response_model=BatchOut,
    **op(
        "knowledge.batch.submit",
        "Ask for approval to apply a prepared batch. The staged result is fixed from this point.",
    ),
)
async def submit_batch(
    batch_id: uuid.UUID,
    person: Annotated[CurrentPerson, require("knowledge.batch.submit")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.submit_batch(person, batch_id)


@router.post(
    "/batches/{batch_id}/approve",
    response_model=BatchOut,
    **op(
        "knowledge.batch.approve",
        "Approve a batch waiting for approval, which applies it live. Only a person may do this.",
        tool=False,
    ),
)
async def approve_batch(
    batch_id: uuid.UUID,
    body: KnowledgeReasonIn,
    person: Annotated[CurrentPerson, require("knowledge.batch.decide")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.approve_batch(person, batch_id, body.reason)


@router.post(
    "/batches/{batch_id}/reject",
    response_model=BatchOut,
    **op(
        "knowledge.batch.reject",
        "Reject a batch waiting for approval, with a reason. Only a person may do this.",
        tool=False,
    ),
)
async def reject_batch(
    batch_id: uuid.UUID,
    body: KnowledgeReasonIn,
    person: Annotated[CurrentPerson, require("knowledge.batch.decide")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.reject_batch(person, batch_id, body.reason)


@router.post(
    "/batches/{batch_id}/cancel",
    response_model=BatchOut,
    **op(
        "knowledge.batch.cancel",
        "Cancel a batch that is still open. A request waiting for approval is withdrawn too.",
    ),
)
async def cancel_batch(
    batch_id: uuid.UUID,
    body: KnowledgeReasonIn,
    person: Annotated[CurrentPerson, require("knowledge.batch.cancel")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.cancel_batch(person, batch_id, body.reason)


@router.post(
    "/batches/{batch_id}/undo",
    response_model=BatchOut,
    dependencies=[confirm_agent_action(UNDO_OPERATION)],
    **op(
        UNDO_OPERATION,
        "Undo an applied batch, restoring the live documents and access rules it replaced. "
        "The person confirms this first.",
        confirm=True,
    ),
)
async def undo_batch(
    batch_id: uuid.UUID,
    person: Annotated[CurrentPerson, require("knowledge.batch.undo")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> BatchOut:
    return await service.undo_batch(person, batch_id)


@router.post(
    "/collections/{collection_id}/documents/erase",
    status_code=204,
    **op(
        "knowledge.document.erase",
        "Erase a document from every batch of a collection, from search and from file storage. "
        "Only a person may do this, with a reason.",
        tool=False,
    ),
)
async def erase_document(
    collection_id: uuid.UUID,
    body: KnowledgeEraseIn,
    person: Annotated[CurrentPerson, require("knowledge.document.erase")],
    service: Annotated[BatchesService, Depends(get_batches_service)],
) -> None:
    await service.erase_document(person, collection_id, body)
