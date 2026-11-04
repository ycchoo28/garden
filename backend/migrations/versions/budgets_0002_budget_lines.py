"""Budgets: versioned budget lines. A replacement adds a version and supersedes the one before."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6a2c9e4f1b08"
down_revision: str | None = "1903176317d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "line",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("line_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_line")),
        sa.UniqueConstraint("line_id", "version", name="uq_line_line_id_version"),
        schema="budgets",
    )
    op.create_index(op.f("ix_line_line_id"), "line", ["line_id"], unique=False, schema="budgets")
    op.create_index(
        "uq_line_current_key",
        "line",
        ["department_id", "year_month", "category"],
        unique=True,
        schema="budgets",
        postgresql_where=sa.text("superseded_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_line_current_key", table_name="line", schema="budgets")
    op.drop_index(op.f("ix_line_line_id"), table_name="line", schema="budgets")
    op.drop_table("line", schema="budgets")
