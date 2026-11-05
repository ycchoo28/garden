"""Record which CLI login made a saved change: the device it named, and when the login was created.

Additive. Every earlier save has no login recorded, so both columns start empty.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c7e3a9f14b26"
down_revision: str | None = "8d2f6b1e4c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "event",
        sa.Column("device", sa.String(length=120), nullable=True),
        schema="audit",
    )
    op.add_column(
        "event",
        sa.Column("login_created_at", sa.DateTime(timezone=True), nullable=True),
        schema="audit",
    )


def downgrade() -> None:
    op.drop_column("event", "login_created_at", schema="audit")
    op.drop_column("event", "device", schema="audit")
