"""Knowledge ingest: collections, access rules, import batches, staged files and chunks."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector  # pyright: ignore[reportMissingTypeStubs]
from sqlalchemy.dialects import postgresql

revision: str = "5e0c8a9d31f4"
down_revision: str | None = "fa629ce601b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMENSIONS = 1024


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "collection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection")),
        sa.UniqueConstraint("name", name=op.f("uq_collection_name")),
        schema="knowledge",
    )
    op.create_table(
        "collection_rule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("all_departments", sa.Boolean(), nullable=False),
        sa.Column(
            "department_codes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("ARRAY[]::text[]"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["knowledge.collection.id"],
            name=op.f("fk_collection_rule_collection_id_collection"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_collection_rule")),
        schema="knowledge",
    )
    op.create_index(
        "ix_collection_rule_collection_id",
        "collection_rule",
        ["collection_id"],
        unique=False,
        schema="knowledge",
    )
    op.create_table(
        "import_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column(
            "report",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["knowledge.collection.id"],
            name=op.f("fk_import_batch_collection_id_collection"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_batch")),
        schema="knowledge",
    )
    op.create_index(
        "ix_import_batch_collection_id",
        "import_batch",
        ["collection_id"],
        unique=False,
        schema="knowledge",
    )
    op.create_table(
        "batch_file",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("translation_group", sa.String(length=200), nullable=False),
        sa.Column("parse_status", sa.String(length=20), nullable=True),
        sa.Column("parse_error", sa.Text(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["knowledge.import_batch.id"],
            name=op.f("fk_batch_file_batch_id_import_batch"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_batch_file")),
        schema="knowledge",
    )
    op.create_index(
        "ix_batch_file_batch_id", "batch_file", ["batch_id"], unique=False, schema="knowledge"
    )
    op.create_table(
        "chunk",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("batch_file_id", sa.Uuid(), nullable=False),
        sa.Column("collection_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("translation_group", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("heading_path", sa.Text(), nullable=False),
        sa.Column("locator", sa.String(length=100), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("context_prefix", sa.Text(), nullable=False),
        sa.Column(
            "identifiers",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("ARRAY[]::text[]"),
            nullable=False,
        ),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMENSIONS), nullable=True),  # pyright: ignore[reportUnknownArgumentType]
        sa.Column(
            "keyword",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple', context_prefix || ' ' || body)", persisted=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["knowledge.import_batch.id"],
            name=op.f("fk_chunk_batch_id_import_batch"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_file_id"],
            ["knowledge.batch_file.id"],
            name=op.f("fk_chunk_batch_file_id_batch_file"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["knowledge.collection.id"],
            name=op.f("fk_chunk_collection_id_collection"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["knowledge.chunk.id"],
            name=op.f("fk_chunk_parent_id_chunk"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk")),
        schema="knowledge",
    )
    op.create_index("ix_chunk_batch_id", "chunk", ["batch_id"], unique=False, schema="knowledge")
    op.create_index(
        "ix_chunk_embedding_hnsw",
        "chunk",
        ["embedding"],
        unique=False,
        schema="knowledge",
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index(
        "ix_chunk_keyword",
        "chunk",
        ["keyword"],
        unique=False,
        schema="knowledge",
        postgresql_using="gin",
    )
    op.create_index(
        "ix_chunk_identifiers",
        "chunk",
        ["identifiers"],
        unique=False,
        schema="knowledge",
        postgresql_using="gin",
    )


def downgrade() -> None:
    op.drop_table("chunk", schema="knowledge")
    op.drop_table("batch_file", schema="knowledge")
    op.drop_table("import_batch", schema="knowledge")
    op.drop_table("collection_rule", schema="knowledge")
    op.drop_table("collection", schema="knowledge")
    op.execute("DROP EXTENSION IF EXISTS vector")
