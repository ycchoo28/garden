"""ORM models for the approvals core module. Every table lives in the approvals schema.

A flow is data: an ordered list of steps, each with the role that decides it and the amount above
which it applies. A run is one approval request for one subject, with its steps copied from the
flow. Decisions and cancellations are facts that are only ever added, so the history is complete.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.shared.db import NAMING_CONVENTION
from app.shared.ids import new_id


class Base(DeclarativeBase):
    metadata = MetaData(schema="approvals", naming_convention=NAMING_CONVENTION)


class Flow(Base):
    """A named approval flow, such as the expense claim flow. Seeded as data."""

    __tablename__ = "flow"

    code: Mapped[str] = mapped_column(String(60), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))


class FlowStep(Base):
    """One step of a flow. `above` is the amount over which the step applies; 0 means always."""

    __tablename__ = "flow_step"
    __table_args__ = (UniqueConstraint("flow_code", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    flow_code: Mapped[str] = mapped_column(String(60), ForeignKey("approvals.flow.code"))
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(50))
    department_scoped: Mapped[bool] = mapped_column(Boolean)
    above: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class Run(Base):
    """One request for approval of one subject. A resubmitted subject gets a new run."""

    __tablename__ = "run"
    __table_args__ = (Index("ix_run_subject", "subject_type", "subject_id"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    flow_code: Mapped[str] = mapped_column(String(60), ForeignKey("approvals.flow.code"))
    subject_type: Mapped[str] = mapped_column(String(40))
    # The subject lives in another module, so it is referenced by id only.
    subject_id: Mapped[uuid.UUID]
    subject_department_id: Mapped[uuid.UUID]
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    started_by: Mapped[uuid.UUID]
    started_by_name: Mapped[str] = mapped_column(String(200))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by: Mapped[uuid.UUID | None]
    cancel_reason: Mapped[str | None] = mapped_column(Text)


class RunStep(Base):
    """A step of one run, copied from its flow when the run starts."""

    __tablename__ = "run_step"
    __table_args__ = (UniqueConstraint("run_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.run.id"))
    position: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(50))
    department_scoped: Mapped[bool] = mapped_column(Boolean)
    above: Mapped[Decimal] = mapped_column(Numeric(14, 2))


class Decision(Base):
    """A decision on one step. A step is decided once, so the unique key also guards races."""

    __tablename__ = "decision"
    __table_args__ = (UniqueConstraint("run_id", "position"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("approvals.run.id"))
    position: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(20))
    decided_by: Mapped[uuid.UUID]
    decided_by_name: Mapped[str] = mapped_column(String(200))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
