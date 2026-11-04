"""HTTP endpoints of the knowledge module."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, UploadFile

from app.config import Settings, get_settings
from app.core.users import CurrentPerson, require
from app.modules.knowledge.schemas import (
    BatchFileOut,
    BatchIn,
    BatchOut,
    CollectionIn,
    CollectionOut,
    PreviewIn,
    PreviewOut,
    StatusOut,
)
from app.modules.knowledge.service import KnowledgeService, get_service
from app.shared.operations import op

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
