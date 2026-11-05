"""Request and response shapes for the knowledge module.

Every class here is prefixed with `Knowledge` where its name could clash with another module.
"""

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

# The roles a collection rule may name. Only roles that exist in the portal can grant reading.
ASSIGNABLE_ROLES: frozenset[str] = frozenset({"employee", "manager", "finance", "admin"})


class StatusOut(BaseModel):
    module: str
    status: str
    database: str


class CollectionRuleIn(BaseModel):
    role: str = Field(min_length=1, max_length=50)
    all_departments: bool = False
    department_codes: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_reach(self) -> Self:
        # A rule must name its reach. A rule with neither all departments nor a department
        # grants nothing.
        if not self.all_departments and not self.department_codes:
            raise ValueError("Name at least one department, or set all_departments.")
        if self.all_departments and self.department_codes:
            raise ValueError("Set all_departments or name departments, not both.")
        return self


class CollectionIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rules: list[CollectionRuleIn] = Field(default_factory=list[CollectionRuleIn], max_length=20)


class CollectionRuleOut(BaseModel):
    role: str
    all_departments: bool
    department_codes: list[str]


class CollectionOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    rules: list[CollectionRuleOut]


class BatchIn(BaseModel):
    mode: Literal["full_set", "partial"]
    # When set, applying the batch replaces the collection's access rules. That is a risk trigger.
    access_rules: list[CollectionRuleIn] | None = Field(default=None, max_length=20)


class BatchFileOut(BaseModel):
    id: uuid.UUID
    sha256: str
    name: str
    content_type: str
    size_bytes: int
    translation_group: str
    parse_status: str | None
    parse_error: str | None
    title: str | None
    language: str | None


class BatchOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    mode: str
    state: str
    failure_reason: str | None
    report: dict[str, Any]
    risk: list[str]
    access_rules: list[CollectionRuleOut] | None
    created_at: datetime
    prepared_at: datetime | None
    applied_at: datetime | None
    undone_at: datetime | None
    files: list[BatchFileOut]


class KnowledgeBatchSummaryOut(BaseModel):
    id: uuid.UUID
    collection_id: uuid.UUID
    collection_name: str
    mode: str
    state: str
    risk: list[str]
    created_at: datetime


class KnowledgeReasonIn(BaseModel):
    """The reason a person gives with an approval, a rejection or a cancellation."""

    reason: str | None = Field(default=None, max_length=500)


class KnowledgeEraseIn(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=500)


class PreviewIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=8000, ge=200, le=8000)


class PreviewHitOut(BaseModel):
    chunk_ids: list[uuid.UUID]
    batch_file_id: uuid.UUID
    title: str
    translation_group: str
    language: str
    heading_path: str
    locator: str
    text: str
    token_count: int
    score: float


class PreviewOut(BaseModel):
    results: list[PreviewHitOut]
    truncated: bool
    token_count: int


class KnowledgeSearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    limit: int = Field(default=5, ge=1, le=20)
    max_tokens: int = Field(default=8000, ge=200, le=8000)
    # Search what was live at this moment. Omit it to search what is live now.
    as_of: datetime | None = None


class KnowledgeSearchHitOut(BaseModel):
    chunk_ids: list[uuid.UUID]
    collection_id: uuid.UUID
    collection_name: str
    title: str
    translation_group: str
    language: str
    heading_path: str
    locator: str
    text: str
    token_count: int
    score: float


class KnowledgeSearchOut(BaseModel):
    results: list[KnowledgeSearchHitOut]
    truncated: bool
    token_count: int
    as_of: datetime | None


class KnowledgeSearchLogOut(BaseModel):
    id: uuid.UUID
    searched_at: datetime
    person_id: uuid.UUID
    person_name: str
    acting_as_agent: bool
    query: str
    as_of: datetime | None
    chunk_ids: list[uuid.UUID]
