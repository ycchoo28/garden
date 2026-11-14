"""Distinguish CLI login sources and expire browser-created logins."""

import sqlalchemy as sa
from alembic import op

revision: str = "7c4e9a1b2d31"
down_revision: str | None = "7c4e9a1b2d30"
branch_labels: str | tuple[str, ...] | None = None
depends_on: str | tuple[str, ...] | None = None


def upgrade() -> None:
    op.add_column(
        "cli_login",
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        schema="users",
    )
    op.add_column(
        "cli_login", sa.Column("source", sa.String(length=8), nullable=True), schema="users"
    )
    op.execute("UPDATE users.cli_login SET source = 'cli' WHERE source IS NULL")
    op.alter_column("cli_login", "source", nullable=False, schema="users")
    op.create_check_constraint(
        op.f("ck_cli_login_source"),
        "cli_login",
        "source IN ('cli', 'web')",
        schema="users",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_cli_login_source"), "cli_login", schema="users", type_="check")
    op.drop_column("cli_login", "source", schema="users")
    op.drop_column("cli_login", "expires_at", schema="users")
