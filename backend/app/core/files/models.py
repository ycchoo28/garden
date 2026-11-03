"""ORM models for the files core module. Every table lives in the files schema."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION


class Base(DeclarativeBase):
    metadata = MetaData(schema="files", naming_convention=NAMING_CONVENTION)


class StoredFile(Base):
    """A file's metadata. The bytes sit on disk under their SHA-256, so uploads share one."""

    __tablename__ = "file"

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int]
    content_type: Mapped[str] = mapped_column(String(100))
    first_name: Mapped[str] = mapped_column(String(255))
    first_uploaded_by: Mapped[uuid.UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
