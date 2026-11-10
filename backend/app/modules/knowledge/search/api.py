"""HTTP endpoints for search, previewing a prepared batch, and reading the search log."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.users import CurrentPerson, require
from app.modules.knowledge.schemas import (
    KnowledgeSearchIn,
    KnowledgeSearchLogOut,
    KnowledgeSearchOut,
    PreviewIn,
    PreviewOut,
)
from app.modules.knowledge.search.service import SearchService, get_search_service
from app.shared.operations import op

router = APIRouter()


@router.post(
    "/batches/{batch_id}/preview",
    response_model=PreviewOut,
    **op(
        "knowledge.batch.preview",
        "Search a prepared batch before it is applied. Results follow the caller's access rules.",
        confirm=False,
    ),
)
async def preview_batch(
    batch_id: uuid.UUID,
    body: PreviewIn,
    person: Annotated[CurrentPerson, require("knowledge.batch.prepare")],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> PreviewOut:
    return await service.preview(person, batch_id, body)


@router.post(
    "/search",
    response_model=KnowledgeSearchOut,
    **op(
        "knowledge.search.run",
        "Search the knowledge the caller may read, as it is live now or was live at a past moment. "
        "Each result names its source document and section.",
        confirm=False,
    ),
)
async def search(
    body: KnowledgeSearchIn,
    person: Annotated[CurrentPerson, require("knowledge.search")],
    service: Annotated[SearchService, Depends(get_search_service)],
) -> KnowledgeSearchOut:
    return await service.search(person, body)


@router.get(
    "/search-log",
    response_model=list[KnowledgeSearchLogOut],
    **op(
        "knowledge.search_log.list",
        "Read who searched for what and which chunks they got back. Only a person may read it.",
        tool=False,
        confirm=False,
    ),
)
async def search_log(
    _person: Annotated[CurrentPerson, require("knowledge.search_log.read")],
    service: Annotated[SearchService, Depends(get_search_service)],
    limit: int = Query(default=100, ge=1, le=500),
) -> list[KnowledgeSearchLogOut]:
    return await service.search_log(limit)
