"""Collection rules: list and read collections, create one with its access rules, and report health.

Endpoints call this, never the database. The batches and search features read a
collection through the public methods here, never through the repository, and they get
plain views back, never ORM rows.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any

from fastapi import Depends

from app.core.audit import AuditService, get_audit_service
from app.core.users import CurrentPerson
from app.modules.knowledge.collections.repository import (
    CollectionRepository,
    get_collection_repository,
)
from app.modules.knowledge.models import Collection, CollectionRule
from app.modules.knowledge.rules import rule_out, rule_specs
from app.modules.knowledge.schemas import (
    CollectionIn,
    CollectionOut,
    StatusOut,
)
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, NotFound
from app.shared.ids import new_id


@dataclass(frozen=True)
class CollectionView:
    """A collection as other features see it. `rules` are plain rule specs, as stored."""

    id: uuid.UUID
    name: str
    created_at: datetime
    rules: list[dict[str, Any]]


class CollectionsService:
    def __init__(self, repo: CollectionRepository, audit: AuditService, clock: Clock) -> None:
        self._repo = repo
        self._audit = audit
        self._clock = clock

    async def status(self) -> StatusOut:
        await self._repo.ping()
        return StatusOut(module="knowledge", status="ok", database="ok")

    async def list_collections(self) -> list[CollectionOut]:
        return [_collection_out(_view_of(row)) for row in await self._repo.list_collections()]

    async def get_collection(self, collection_id: uuid.UUID) -> CollectionOut:
        return _collection_out(await self.collection(collection_id))

    async def create_collection(self, actor: CurrentPerson, body: CollectionIn) -> CollectionOut:
        if await self._repo.collection_name_taken(body.name):
            raise Conflict("A collection with this name already exists.")
        rules = [CollectionRule(**spec) for spec in rule_specs(body.rules)]
        collection = Collection(
            id=new_id(),
            name=body.name,
            created_by=actor.id,
            created_at=self._clock.now(),
            rules=rules,
        )
        self._repo.add(collection)
        self._audit.record(
            actor_id=actor.id,
            operation="knowledge.collection.create",
            target=f"collection:{collection.id}",
            summary=f"Created collection {body.name} with {len(rules)} access rule(s).",
        )
        await self._repo.commit()
        return _collection_out(_view_of(collection))

    async def collection(self, collection_id: uuid.UUID) -> CollectionView:
        """The collection with its rules. Other features call this before they change one."""
        collection = await self._repo.get_collection(collection_id)
        if collection is None:
            raise NotFound("The collection does not exist.")
        return _view_of(collection)

    async def replace_rules(
        self, collection_id: uuid.UUID, specs: Sequence[Mapping[str, Any]]
    ) -> None:
        """Set a collection's access rules. The calling request's transaction commits."""
        if not await self._repo.replace_rules(collection_id, specs):
            raise NotFound("The collection does not exist.")

    async def names(self, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
        return await self._repo.collection_names(ids)


def _view_of(collection: Collection) -> CollectionView:
    return CollectionView(
        id=collection.id,
        name=collection.name,
        created_at=collection.created_at,
        rules=[
            {
                "role": rule.role,
                "all_departments": rule.all_departments,
                "department_codes": list(rule.department_codes),
            }
            for rule in collection.rules
        ],
    )


def _collection_out(collection: CollectionView) -> CollectionOut:
    return CollectionOut(
        id=collection.id,
        name=collection.name,
        created_at=collection.created_at,
        rules=[rule_out(spec) for spec in collection.rules],
    )


def get_collections_service(
    repo: Annotated[CollectionRepository, Depends(get_collection_repository)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> CollectionsService:
    return CollectionsService(repo, audit, clock)
