"""Agent core: confirmations waiting for the person's approval, and model usage counts per request.

Prompts and completions are never stored.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a6e3c1d8f2b5"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("agent",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS agent")
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
        sa.Column("request_summary", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_confirmation")),
        schema="agent",
    )
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
        schema="agent",
    )
    op.create_index(
        op.f("ix_usage_person_id_occurred_at"),
        "usage",
        ["person_id", "occurred_at"],
        unique=False,
        schema="agent",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_usage_person_id_occurred_at"), table_name="usage", schema="agent")
    op.drop_table("usage", schema="agent")
    op.drop_table("confirmation", schema="agent")
    op.execute("DROP SCHEMA IF EXISTS agent")
