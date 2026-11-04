"""Claims: expense claims and their lines."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "17a123343398"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("claims",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS claims")
    op.create_table(
        "claim",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("claimant_id", sa.Uuid(), nullable=False),
        sa.Column("claimant_name", sa.String(length=200), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("department_code", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim")),
        schema="claims",
    )
    op.create_table(
        "line",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=False),
        sa.Column("spent_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("added_by", sa.Uuid(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("voided_by", sa.Uuid(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["claim_id"], ["claims.claim.id"], name=op.f("fk_line_claim_id_claim")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_line")),
        schema="claims",
    )


def downgrade() -> None:
    op.drop_table("line", schema="claims")
    op.drop_table("claim", schema="claims")
    op.execute("DROP SCHEMA IF EXISTS claims")
