"""Gateway core: usage counts per forwarded request. Prompts and completions are never stored."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b81f4c6d2e95"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("gateway",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS gateway")
    op.create_table(
        "usage",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("cli_login_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_usage")),
        schema="gateway",
    )
    op.create_index(
        op.f("ix_usage_person_id_occurred_at"),
        "usage",
        ["person_id", "occurred_at"],
        unique=False,
        schema="gateway",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_person_id_occurred_at"), table_name="usage", schema="gateway")
    op.drop_table("usage", schema="gateway")
    op.execute("DROP SCHEMA IF EXISTS gateway")
