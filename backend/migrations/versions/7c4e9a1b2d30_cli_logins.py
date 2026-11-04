"""Users core: CLI logins, one per device that runs the garden CLI or its MCP server."""

import sqlalchemy as sa
from alembic import op

revision: str = "7c4e9a1b2d30"
down_revision: str | None = "0d64109fa594"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.create_table(
        "cli_login",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("device", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["users.person.id"],
            name=op.f("fk_cli_login_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cli_login")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_cli_login_token_hash")),
        schema="users",
    )


def downgrade() -> None:
    op.drop_table("cli_login", schema="users")
