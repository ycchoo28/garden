"""Request and response shapes for the knowledge module."""

import uuid
from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.modules.knowledge.permissions import KNOWLEDGE_ADMIN

# The roles a collection rule may name. Only roles that exist in the portal can grant reading.
ASSIGNABLE_ROLES: frozenset[str] = frozenset(
    {"employee", "manager", "finance", KNOWLEDGE_ADMIN, "admin"}
)


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
    created_at: datetime
    prepared_at: datetime | None
    files: list[BatchFileOut]


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
