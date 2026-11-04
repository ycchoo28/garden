"""ORM models for the users core module. Every table lives in the users schema."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, MetaData, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="users", naming_convention=NAMING_CONVENTION)


class Department(Base):
    """A department of the company. People belong to exactly one."""

    __tablename__ = "department"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(50), unique=True)
    name: Mapped[str] = mapped_column(String(100))


class Person(Base):
    """Someone who may sign in. The OIDC subject is the stable key that sign-in finds them by."""

    __tablename__ = "person"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    subject: Mapped[str] = mapped_column(String(255), unique=True)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.department.id"))
    is_active: Mapped[bool] = mapped_column(default=True)


class RoleGrant(Base):
    """One role a person holds, beyond the `employee` role every signed-in person has."""

    __tablename__ = "role_grant"
    __table_args__ = (UniqueConstraint("person_id", "role"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.person.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(50))


class WebSession(Base):
    """A browser session. The cookie holds a random token; only its SHA-256 hash is stored."""

    __tablename__ = "web_session"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.person.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SignInAttempt(Base):
    """A sign-in that has started and not finished. The PKCE verifier and nonce wait here."""

    __tablename__ = "sign_in_attempt"

    state: Mapped[str] = mapped_column(String(64), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(128))
    nonce: Mapped[str] = mapped_column(String(64))
    return_to: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CliLogin(Base):
    """A device's sign-in for the garden CLI and its MCP server. Only the token's SHA-256 is stored.

    A CLI login holds its person's permissions minus the person-only ones. Revoking sets
    `revoked_at`, so the row stays as a record of when the device could last act.
    """

    __tablename__ = "cli_login"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    person_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.person.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    device: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
