"""Audit core: saved changes, written in the same transaction as the change."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5b1f0c2e7a10"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("audit",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS audit")
    op.create_table(
        "event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("acting_as_agent", sa.Boolean(), nullable=False),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("target", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event")),
        schema="audit",
    )


def downgrade() -> None:
    op.drop_table("event", schema="audit")
    op.execute("DROP SCHEMA IF EXISTS audit")
