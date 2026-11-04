"""ORM models. Every table lives in the spend schema."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, MetaData, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="spend", naming_convention=NAMING_CONVENTION)


class Statement(Base):
    """One imported card statement. A void keeps the row and records why it stopped counting."""

    __tablename__ = "statement"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    source_name: Mapped[str] = mapped_column(String(255))
    source_sha256: Mapped[str] = mapped_column(String(64))
    row_count: Mapped[int]
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # People live in the users schema, so this is a plain id: schemas never foreign-key each other.
    imported_by: Mapped[uuid.UUID]
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[uuid.UUID | None]
    void_reason: Mapped[str | None] = mapped_column(Text)


class StatementRow(Base):
    """One charge or credit from a statement. A voided statement keeps its rows."""

    __tablename__ = "statement_row"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    statement_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("spend.statement.id", ondelete="CASCADE"), index=True
    )
    line_no: Mapped[int]
    transaction_ref: Mapped[str] = mapped_column(String(120), index=True)
    posted_on: Mapped[date] = mapped_column(Date)
    year_month: Mapped[str] = mapped_column(String(7), index=True)
    # Departments live in the users schema, so this is a plain id.
    department_id: Mapped[uuid.UUID] = mapped_column(index=True)
    category: Mapped[str] = mapped_column(String(40))
    merchant: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class MonthClose(Base):
    """A closed month. Once it exists, no spend for that month can be imported or voided."""

    __tablename__ = "month_close"

    year_month: Mapped[str] = mapped_column(String(7), primary_key=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[uuid.UUID]
    reason: Mapped[str] = mapped_column(Text)
