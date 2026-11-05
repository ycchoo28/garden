"""ORM models for the audit core module. Every table lives in the audit schema."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, Text, Uuid
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="audit", naming_convention=NAMING_CONVENTION)


class AuditEvent(Base):
    """One saved change. Written in the same transaction as the change it records."""

    __tablename__ = "event"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # A person in the users schema, as a plain id: schemas may not foreign-key each other.
    actor_id: Mapped[uuid.UUID]
    acting_as_agent: Mapped[bool]
    operation: Mapped[str] = mapped_column(String(120))
    target: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text)
    # Set when the change ran under a confirmation the person approved in the browser (ADR 0012).
    confirmation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the change was made through a CLI login: the device the login named, and when the
    # login was created (ADR 0031, the agent design).
    device: Mapped[str | None] = mapped_column(String(120))
    login_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
