"""Create the spend schema."""

from collections.abc import Sequence

from alembic import op

revision: str = "922e637df8e9"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("spend",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS spend")


def downgrade() -> None:
    op.execute("DROP SCHEMA spend")
