"""Spend: card statements, their rows, and closed months."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d3a1f58c42"
down_revision: str | None = "922e637df8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "statement",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_name", sa.String(length=255), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("imported_by", sa.Uuid(), nullable=False),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("voided_by", sa.Uuid(), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statement")),
        schema="spend",
    )
    op.create_table(
        "statement_row",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("statement_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("transaction_ref", sa.String(length=120), nullable=False),
        sa.Column("posted_on", sa.Date(), nullable=False),
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("merchant", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["statement_id"],
            ["spend.statement.id"],
            name=op.f("fk_statement_row_statement_id_statement"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_statement_row")),
        schema="spend",
    )
    op.create_index(
        op.f("ix_statement_row_statement_id"),
        "statement_row",
        ["statement_id"],
        unique=False,
        schema="spend",
    )
    op.create_index(
        op.f("ix_statement_row_transaction_ref"),
        "statement_row",
        ["transaction_ref"],
        unique=False,
        schema="spend",
    )
    op.create_index(
        op.f("ix_statement_row_year_month"),
        "statement_row",
        ["year_month"],
        unique=False,
        schema="spend",
    )
    op.create_index(
        op.f("ix_statement_row_department_id"),
        "statement_row",
        ["department_id"],
        unique=False,
        schema="spend",
    )
    op.create_table(
        "month_close",
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("year_month", name=op.f("pk_month_close")),
        schema="spend",
    )


def downgrade() -> None:
    op.drop_table("month_close", schema="spend")
    op.drop_table("statement_row", schema="spend")
    op.drop_table("statement", schema="spend")
