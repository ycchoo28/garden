"""Create the knowledge schema."""

from collections.abc import Sequence

from alembic import op

revision: str = "fa629ce601b5"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("knowledge",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS knowledge")


def downgrade() -> None:
    op.execute("DROP SCHEMA knowledge")
