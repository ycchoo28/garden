"""Audit core: a change made under an approved confirmation names the approval and its time."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8d2f6b1e4c93"
down_revision: str | None = "5b1f0c2e7a10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("event", sa.Column("confirmation_id", sa.Uuid(), nullable=True), schema="audit")
    op.add_column(
        "event",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        schema="audit",
    )


def downgrade() -> None:
    op.drop_column("event", "confirmed_at", schema="audit")
    op.drop_column("event", "confirmation_id", schema="audit")
