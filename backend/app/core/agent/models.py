"""ORM models for the agent core module. Every table lives in the agent schema."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, MetaData, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="agent", naming_convention=NAMING_CONVENTION)


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
    # What the person reads before deciding: the method, the target and the request body as JSON.
    request_summary: Mapped[str | None] = mapped_column(Text)


class Usage(Base):
    """One row per accepted model request. Counts only: prompts and completions are never kept."""

    __tablename__ = "usage"
    __table_args__ = (Index("ix_usage_person_id_occurred_at", "person_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    person_id: Mapped[uuid.UUID]
    # Null for a request made from the browser session rather than a CLI login.
    cli_login_id: Mapped[uuid.UUID | None]
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    model: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20))
    prompt_tokens: Mapped[int] = mapped_column(Integer)
    completion_tokens: Mapped[int] = mapped_column(Integer)
    total_tokens: Mapped[int] = mapped_column(Integer)
