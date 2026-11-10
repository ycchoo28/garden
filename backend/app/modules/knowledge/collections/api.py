"""HTTP endpoints for knowledge collections and the module's health check."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.users import CurrentPerson, require
from app.modules.knowledge.collections.service import CollectionsService, get_collections_service
from app.modules.knowledge.schemas import CollectionIn, CollectionOut, StatusOut
from app.shared.operations import op

router = APIRouter()


@router.get(
    "/status",
    response_model=StatusOut,
    **op(
        "knowledge.status.read",
        "Read whether the knowledge module is up and can reach its database.",
        confirm=False,
    ),
)
async def read_status(
    _person: Annotated[CurrentPerson, require("knowledge.collection.view")],
    service: Annotated[CollectionsService, Depends(get_collections_service)],
) -> StatusOut:
    return await service.status()


@router.get(
    "/collections",
    response_model=list[CollectionOut],
    **op(
        "knowledge.collection.list",
        "List every knowledge collection and its access rules.",
        confirm=False,
    ),
)
async def list_collections(
    _person: Annotated[CurrentPerson, require("knowledge.collection.view")],
    service: Annotated[CollectionsService, Depends(get_collections_service)],
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
        confirm=False,
    ),
)
async def create_collection(
    body: CollectionIn,
    person: Annotated[CurrentPerson, require("knowledge.collection.manage")],
    service: Annotated[CollectionsService, Depends(get_collections_service)],
) -> CollectionOut:
    return await service.create_collection(person, body)


@router.get(
    "/collections/{collection_id}",
    response_model=CollectionOut,
    **op(
        "knowledge.collection.read",
        "Read one knowledge collection and its access rules.",
        confirm=False,
    ),
)
async def read_collection(
    collection_id: uuid.UUID,
    _person: Annotated[CurrentPerson, require("knowledge.collection.view")],
    service: Annotated[CollectionsService, Depends(get_collections_service)],
) -> CollectionOut:
    return await service.get_collection(collection_id)
