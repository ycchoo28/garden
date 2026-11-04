"""ORM models for the gateway core module. Every table lives in the gateway schema."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Index, Integer, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="gateway", naming_convention=NAMING_CONVENTION)


class Usage(Base):
    """One row per accepted request. Counts only: prompts and completions are never kept."""

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
