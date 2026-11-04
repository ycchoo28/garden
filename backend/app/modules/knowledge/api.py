"""HTTP endpoints of the knowledge module."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Query, UploadFile

from app.config import Settings, get_settings
from app.core.confirmations import confirm_agent_action
from app.core.users import CurrentPerson, require
from app.modules.knowledge.schemas import (
    BatchFileOut,
    BatchIn,
    BatchOut,
    CollectionIn,
    CollectionOut,
    KnowledgeBatchSummaryOut,
    KnowledgeEraseIn,
    KnowledgeReasonIn,
    KnowledgeSearchIn,
    KnowledgeSearchLogOut,
    KnowledgeSearchOut,
    PreviewIn,
    PreviewOut,
    StatusOut,
)
from app.modules.knowledge.service import KnowledgeService, get_service
from app.shared.operations import op

UNDO_OPERATION = "knowledge.batch.undo"

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


@router.get(
    "/status",
    response_model=StatusOut,
    **op(
        "knowledge.status.read",
        "Read whether the knowledge module is up and can reach its database.",
    ),
)
async def read_status(
    _person: Annotated[CurrentPerson, require("knowledge.collection.view")],
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> StatusOut:
    return await service.status()


@router.get(
    "/collections",
    response_model=list[CollectionOut],
    **op("knowledge.collection.list", "List every knowledge collection and its access rules."),
)
async def list_collections(
    _person: Annotated[CurrentPerson, require("knowledge.collection.view")],
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> list[CollectionOut]:
    return await service.list_collections()


@router.post(
    "/collections",
    response_model=CollectionOut,
    status_code=201,
    **op(
        "knowledge.collection.create",
        "Create a knowledge collection with its access rules. Only a person may do this.",
        tool=False,
    ),
)
async def create_collection(
    body: CollectionIn,
    person: Annotated[CurrentPerson, require("knowledge.collection.manage")],
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> CollectionOut:
    return await service.create_collection(person, body)


@router.get(
    "/collections/{collection_id}",
    response_model=CollectionOut,
    **op("knowledge.collection.read", "Read one knowledge collection and its access rules."),
)
async def read_collection(
    collection_id: uuid.UUID,
    _person: Annotated[CurrentPerson, require("knowledge.collection.view")],
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> CollectionOut:
    return await service.get_collection(collection_id)


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
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> BatchOut:
    return await service.create_batch(person, collection_id, body)


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
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> None:
    await service.erase_document(person, collection_id, body)


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
    service: Annotated[KnowledgeService, Depends(get_service)],
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
    service: Annotated[KnowledgeService, Depends(get_service)],
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
    service: Annotated[KnowledgeService, Depends(get_service)],
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
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> BatchOut:
    return await service.prepare_batch(person, batch_id)


@router.post(
    "/batches/{batch_id}/preview",
    response_model=PreviewOut,
    **op(
        "knowledge.batch.preview",
        "Search a prepared batch before it is applied. Results follow the caller's access rules.",
    ),
)
async def preview_batch(
    batch_id: uuid.UUID,
    body: PreviewIn,
    person: Annotated[CurrentPerson, require("knowledge.batch.prepare")],
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> PreviewOut:
    return await service.preview(person, batch_id, body)


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
    service: Annotated[KnowledgeService, Depends(get_service)],
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
    service: Annotated[KnowledgeService, Depends(get_service)],
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
    service: Annotated[KnowledgeService, Depends(get_service)],
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
    service: Annotated[KnowledgeService, Depends(get_service)],
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
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> BatchOut:
    return await service.undo_batch(person, batch_id)


@router.post(
    "/search",
    response_model=KnowledgeSearchOut,
    **op(
        "knowledge.search.run",
        "Search the knowledge the caller may read, as it is live now or was live at a past moment. "
        "Each result names its source document and section.",
    ),
)
async def search(
    body: KnowledgeSearchIn,
    person: Annotated[CurrentPerson, require("knowledge.search")],
    service: Annotated[KnowledgeService, Depends(get_service)],
) -> KnowledgeSearchOut:
    return await service.search(person, body)


@router.get(
    "/search-log",
    response_model=list[KnowledgeSearchLogOut],
    **op(
        "knowledge.search_log.list",
        "Read who searched for what and which chunks they got back. Only a person may read it.",
        tool=False,
    ),
)
async def search_log(
    _person: Annotated[CurrentPerson, require("knowledge.search_log.read")],
    service: Annotated[KnowledgeService, Depends(get_service)],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[KnowledgeSearchLogOut]:
    return await service.search_log(limit)
