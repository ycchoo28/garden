"""Approvals core: flows as data, runs, their steps and decisions."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5e2a7c9d1f40"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = ("approvals",)
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS approvals")
    op.create_table(
        "flow",
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_flow")),
        schema="approvals",
    )
    op.create_table(
        "flow_step",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flow_code", sa.String(length=60), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("department_scoped", sa.Boolean(), nullable=False),
        sa.Column("above", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["flow_code"], ["approvals.flow.code"], name=op.f("fk_flow_step_flow_code_flow")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_flow_step")),
        sa.UniqueConstraint("flow_code", "position", name=op.f("uq_flow_step_flow_code")),
        schema="approvals",
    )
    op.create_table(
        "run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("flow_code", sa.String(length=60), nullable=False),
        sa.Column("subject_type", sa.String(length=40), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("subject_department_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("started_by", sa.Uuid(), nullable=False),
        sa.Column("started_by_name", sa.String(length=200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["flow_code"], ["approvals.flow.code"], name=op.f("fk_run_flow_code_flow")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run")),
        schema="approvals",
    )
    op.create_index(
        "ix_run_subject", "run", ["subject_type", "subject_id"], unique=False, schema="approvals"
    )
    op.create_table(
        "run_step",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("department_scoped", sa.Boolean(), nullable=False),
        sa.Column("above", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], ["approvals.run.id"], name=op.f("fk_run_step_run_id_run")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_run_step")),
        sa.UniqueConstraint("run_id", "position", name=op.f("uq_run_step_run_id")),
        schema="approvals",
    )
    op.create_table(
        "decision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("decided_by", sa.Uuid(), nullable=False),
        sa.Column("decided_by_name", sa.String(length=200), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["run_id"], ["approvals.run.id"], name=op.f("fk_decision_run_id_run")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decision")),
        sa.UniqueConstraint("run_id", "position", name=op.f("uq_decision_run_id")),
        schema="approvals",
    )


def downgrade() -> None:
    op.drop_table("decision", schema="approvals")
    op.drop_table("run_step", schema="approvals")
    op.drop_index("ix_run_subject", table_name="run", schema="approvals")
    op.drop_table("run", schema="approvals")
    op.drop_table("flow_step", schema="approvals")
    op.drop_table("flow", schema="approvals")
    op.execute("DROP SCHEMA IF EXISTS approvals")
