"""ORM models for the confirmations core module. Every table lives in the confirmations schema."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="confirmations", naming_convention=NAMING_CONVENTION)


class Confirmation(Base):
    """A critical action an agent asked for, waiting for the person to approve it in the browser.

    It is bound to the person, the CLI login, the operation and the exact request. It works once.
    """

    __tablename__ = "confirmation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    person_id: Mapped[uuid.UUID]
    login_fingerprint: Mapped[str] = mapped_column(String(64))
    operation: Mapped[str] = mapped_column(String(120))
    method: Mapped[str] = mapped_column(String(10))
    target: Mapped[str] = mapped_column(String(300))
    args_digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved: Mapped[bool | None]
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
