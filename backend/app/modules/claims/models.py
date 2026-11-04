"""ORM models for the claims module. Every table lives in the claims schema.

A claim is never edited in place: its lines are added or voided, and each change is a new fact.
Its status is not stored. It is computed from the approval run that the claim started.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Integer, MetaData, Numeric, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="claims", naming_convention=NAMING_CONVENTION)


class Claim(Base):
    """An expense claim. The claimant and department are copied in as facts at creation."""

    __tablename__ = "claim"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    # People live in the users schema, so the claimant is referenced by id only.
    claimant_id: Mapped[uuid.UUID]
    claimant_name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[uuid.UUID]
    department_code: Mapped[str] = mapped_column(String(50))
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Optimistic lock. Every change to the claim moves it on, and a stale writer gets a 409.
    version: Mapped[int] = mapped_column(Integer, default=1)


class Line(Base):
    """One categorised expense with its receipt. A line is voided, never edited or deleted."""

    __tablename__ = "line"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.claim.id"))
    category: Mapped[str] = mapped_column(String(40))
    spent_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    description: Mapped[str] = mapped_column(String(300))
    # The receipt is a stored file in the files schema, referenced by its SHA-256.
    receipt_sha256: Mapped[str] = mapped_column(String(64))
    added_by: Mapped[uuid.UUID]
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    voided_by: Mapped[uuid.UUID | None]
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
