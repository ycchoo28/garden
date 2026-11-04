"""Confirmations core: the request summary that the confirmation page shows the person."""

import sqlalchemy as sa
from alembic import op

revision: str = "3a9d5e7f1c42"
down_revision: str | None = "2f8a6d1e9c05"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "confirmation",
        sa.Column("request_summary", sa.Text(), nullable=True),
        schema="confirmations",
    )


def downgrade() -> None:
    op.drop_column("confirmation", "request_summary", schema="confirmations")
