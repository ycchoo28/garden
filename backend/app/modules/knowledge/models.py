"""ORM models. Every table lives in the knowledge schema."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy import (
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.modules.knowledge.embedding import EMBEDDING_DIMENSIONS
from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="knowledge", naming_convention=NAMING_CONVENTION)


class Collection(Base):
    """A set of documents that one access policy covers."""

    __tablename__ = "collection"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rules: Mapped[list["CollectionRule"]] = relationship(
        back_populates="collection", cascade="all, delete-orphan", lazy="raise"
    )


class CollectionRule(Base):
    """Grants reading to people who hold `role` and work in one of the listed departments.

    A rule reaches every department only when `all_departments` is set. A collection with no rules
    is readable by nobody.
    """

    __tablename__ = "collection_rule"
    __table_args__ = (Index("ix_collection_rule_collection_id", "collection_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.collection.id", ondelete="CASCADE")
    )
    role: Mapped[str] = mapped_column(String(50))
    all_departments: Mapped[bool] = mapped_column(Boolean, default=False)
    department_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("ARRAY[]::text[]")
    )
    collection: Mapped[Collection] = relationship(back_populates="rules", lazy="raise")


class ImportBatch(Base):
    """One handover of documents to a collection. Its files are staged until it is applied."""

    __tablename__ = "import_batch"
    __table_args__ = (Index("ix_import_batch_collection_id", "collection_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.collection.id", ondelete="CASCADE")
    )
    mode: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(30))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    report: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    prepared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BatchFile(Base):
    """A file in a batch. The bytes live in the files core, under their SHA-256."""

    __tablename__ = "batch_file"
    __table_args__ = (Index("ix_batch_file_batch_id", "batch_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.import_batch.id", ondelete="CASCADE")
    )
    sha256: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int]
    translation_group: Mapped[str] = mapped_column(String(200))
    parse_status: Mapped[str | None] = mapped_column(String(20))
    parse_error: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(String(300))
    language: Mapped[str | None] = mapped_column(String(10))


class Chunk(Base):
    """A searchable piece of a staged file.

    Children carry embeddings, parents are kept for context.
    """

    __tablename__ = "chunk"
    __table_args__ = (
        Index("ix_chunk_batch_id", "batch_id"),
        Index(
            "ix_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_chunk_keyword", "keyword", postgresql_using="gin"),
        Index("ix_chunk_identifiers", "identifiers", postgresql_using="gin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.import_batch.id", ondelete="CASCADE")
    )
    batch_file_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.batch_file.id", ondelete="CASCADE")
    )
    collection_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("knowledge.collection.id", ondelete="CASCADE")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("knowledge.chunk.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(10))
    position: Mapped[int] = mapped_column(Integer)
    translation_group: Mapped[str] = mapped_column(String(200))
    title: Mapped[str] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(10))
    heading_path: Mapped[str] = mapped_column(Text)
    locator: Mapped[str] = mapped_column(String(100))
    body: Mapped[str] = mapped_column(Text)
    context_prefix: Mapped[str] = mapped_column(Text)
    identifiers: Mapped[list[str]] = mapped_column(
        ARRAY(Text), default=list, server_default=text("ARRAY[]::text[]")
    )
    token_count: Mapped[int] = mapped_column(Integer)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    keyword: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple', context_prefix || ' ' || body)", persisted=True),
    )
