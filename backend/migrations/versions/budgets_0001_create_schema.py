"""Create the budgets schema."""

from collections.abc import Sequence

from alembic import op

revision: str = "1903176317d4"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("budgets",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS budgets")


def downgrade() -> None:
    op.execute("DROP SCHEMA budgets")
