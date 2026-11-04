"""Knowledge go-live: approval and undo on batches, chunk validity spans, and the search log."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c3a7f1e0b9d2"
down_revision: str | None = "5e0c8a9d31f4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "import_batch",
        sa.Column(
            "access_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column(
            "previous_access_rules",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column(
            "risk",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column("staged_digest", sa.String(length=64), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column("approval_run_id", sa.Uuid(), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column("applied_by", sa.Uuid(), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        schema="knowledge",
    )
    op.add_column(
        "import_batch",
        sa.Column("undone_by", sa.Uuid(), nullable=True),
        schema="knowledge",
    )
    op.create_table(
        "chunk_range",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("live_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("live_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_by_batch_id", sa.Uuid(), nullable=False),
        sa.Column("closed_by_batch_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["knowledge.chunk.id"],
            name=op.f("fk_chunk_range_chunk_id_chunk"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by_batch_id"],
            ["knowledge.import_batch.id"],
            name=op.f("fk_chunk_range_opened_by_batch_id_import_batch"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["closed_by_batch_id"],
            ["knowledge.import_batch.id"],
            name=op.f("fk_chunk_range_closed_by_batch_id_import_batch"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_range")),
        schema="knowledge",
    )
    op.create_index(
        "ix_chunk_range_chunk_id", "chunk_range", ["chunk_id"], unique=False, schema="knowledge"
    )
    op.create_table(
        "search_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("person_name", sa.String(length=200), nullable=False),
        sa.Column("acting_as_agent", sa.Boolean(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "chunk_ids",
            postgresql.ARRAY(sa.Uuid()),
            server_default=sa.text("ARRAY[]::uuid[]"),
            nullable=False,
        ),
        sa.Column("searched_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_search_log")),
        schema="knowledge",
    )
    op.create_index(
        "ix_search_log_searched_at", "search_log", ["searched_at"], unique=False, schema="knowledge"
    )


def downgrade() -> None:
    op.drop_table("search_log", schema="knowledge")
    op.drop_table("chunk_range", schema="knowledge")
    for column in (
        "undone_by",
        "undone_at",
        "applied_by",
        "applied_at",
        "approval_run_id",
        "staged_digest",
        "risk",
        "previous_access_rules",
        "access_rules",
    ):
        op.drop_column("import_batch", column, schema="knowledge")
