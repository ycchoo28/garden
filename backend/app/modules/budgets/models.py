"""ORM models. Every table lives in the budgets schema."""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Index, MetaData, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="budgets", naming_convention=NAMING_CONVENTION)


class BudgetLine(Base):
    """One version of a budget line. A replacement adds a version and supersedes the one before.

    A line keeps its `line_id` across versions. Only one version per line is current, which is the
    one with no `superseded_at`, and only one current version exists per department, month and
    category.
    """

    __tablename__ = "line"
    __table_args__ = (
        UniqueConstraint("line_id", "version", name="uq_line_line_id_version"),
        Index(
            "uq_line_current_key",
            "department_id",
            "year_month",
            "category",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    line_id: Mapped[uuid.UUID] = mapped_column(index=True)
    version: Mapped[int]
    # Departments live in the users schema, so this is a plain id.
    department_id: Mapped[uuid.UUID]
    year_month: Mapped[str] = mapped_column(String(7))
    category: Mapped[str] = mapped_column(String(40))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID]
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None]


class MonthClose(Base):
    """A closed month. Once it exists, no claim line for that month can be added or changed."""

    __tablename__ = "month_close"

    year_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID]
    reason: Mapped[str] = mapped_column(Text)
