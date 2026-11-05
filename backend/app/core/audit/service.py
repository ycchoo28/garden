"""Recording and reading the audit trail."""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import Depends

from app.core.audit.models import AuditEvent
from app.core.audit.repository import AuditRepository, get_audit_repository
from app.shared.clock import Clock, get_clock
from app.shared.request_login import VIA_AGENT, current_request_login


@dataclass(frozen=True)
class AuditEntry:
    id: uuid.UUID
    occurred_at: datetime
    actor_id: uuid.UUID
    acting_as_agent: bool
    operation: str
    target: str
    summary: str
    confirmation_id: uuid.UUID | None
    confirmed_at: datetime | None


class AuditService:
    def __init__(self, repo: AuditRepository, clock: Clock) -> None:
        self._repo = repo
        self._clock = clock

    def record(self, *, actor_id: uuid.UUID, operation: str, target: str, summary: str) -> None:
        """Add the row to the caller's transaction. The caller commits, so the change and its record
        are saved together or not at all."""
        login = current_request_login.get()
        self._repo.add(
            AuditEvent(
                occurred_at=self._clock.now(),
                actor_id=actor_id,
                acting_as_agent=login is not None and login.via == VIA_AGENT,
                operation=operation,
                target=target,
                summary=summary,
                confirmation_id=(
                    uuid.UUID(login.confirmation_id)
                    if login is not None and login.confirmation_id is not None
                    else None
                ),
                confirmed_at=login.confirmed_at if login is not None else None,
            )
        )

    async def recent(self, limit: int) -> list[AuditEntry]:
        rows: Sequence[AuditEvent] = await self._repo.recent(limit)
        return [
            AuditEntry(
                id=row.id,
                occurred_at=row.occurred_at,
                actor_id=row.actor_id,
                acting_as_agent=row.acting_as_agent,
                operation=row.operation,
                target=row.target,
                summary=row.summary,
                confirmation_id=row.confirmation_id,
                confirmed_at=row.confirmed_at,
            )
            for row in rows
        ]


def get_audit_service(
    repo: Annotated[AuditRepository, Depends(get_audit_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> AuditService:
    return AuditService(repo, clock)
