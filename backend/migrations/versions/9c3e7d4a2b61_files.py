"""Files core: content-addressed file metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c3e7d4a2b61"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("files",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS files")
    op.create_table(
        "file",
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("first_name", sa.String(length=255), nullable=False),
        sa.Column("first_uploaded_by", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("sha256", name=op.f("pk_file")),
        schema="files",
    )


def downgrade() -> None:
    op.drop_table("file", schema="files")
    op.execute("DROP SCHEMA IF EXISTS files")
