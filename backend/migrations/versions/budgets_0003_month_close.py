"""Budgets: closed months. Once a month is closed, no claim line for it can be added or changed."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3b7c5a19d24"
down_revision: str | None = "6a2c9e4f1b08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "month_close",
        sa.Column("year_month", sa.String(length=7), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_by", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("year_month", name=op.f("pk_month_close")),
        schema="budgets",
    )


def downgrade() -> None:
    op.drop_table("month_close", schema="budgets")
