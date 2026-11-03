"""Confirmations core: agent actions waiting for the person's approval."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "2f8a6d1e9c05"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("confirmations",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS confirmations")
    op.create_table(
        "confirmation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("login_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("operation", sa.String(length=120), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("target", sa.String(length=300), nullable=False),
        sa.Column("args_digest", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved", sa.Boolean(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_confirmation")),
        schema="confirmations",
    )


def downgrade() -> None:
    op.drop_table("confirmation", schema="confirmations")
    op.execute("DROP SCHEMA IF EXISTS confirmations")
