"""Users core: departments, people, role grants, browser sessions and sign-in attempts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0d64109fa594"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("users",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS users")
    op.create_table(
        "department",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_department")),
        sa.UniqueConstraint("code", name=op.f("uq_department_code")),
        schema="users",
    )
    op.create_table(
        "sign_in_attempt",
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("return_to", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("state", name=op.f("pk_sign_in_attempt")),
        schema="users",
    )
    op.create_table(
        "person",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("department_id", sa.Uuid(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["department_id"],
            ["users.department.id"],
            name=op.f("fk_person_department_id_department"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person")),
        sa.UniqueConstraint("email", name=op.f("uq_person_email")),
        sa.UniqueConstraint("subject", name=op.f("uq_person_subject")),
        schema="users",
    )
    op.create_table(
        "role_grant",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["users.person.id"],
            name=op.f("fk_role_grant_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_grant")),
        sa.UniqueConstraint("person_id", "role", name=op.f("uq_role_grant_person_id")),
        schema="users",
    )
    op.create_table(
        "web_session",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["users.person.id"],
            name=op.f("fk_web_session_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("token_hash", name=op.f("pk_web_session")),
        schema="users",
    )


def downgrade() -> None:
    op.drop_table("web_session", schema="users")
    op.drop_table("role_grant", schema="users")
    op.drop_table("person", schema="users")
    op.drop_table("sign_in_attempt", schema="users")
    op.drop_table("department", schema="users")
    op.execute("DROP SCHEMA IF EXISTS users")
