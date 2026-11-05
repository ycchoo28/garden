"""HTTP routes of the audit module."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.audit.schemas import AuditEventOut
from app.core.audit.service import AuditService, get_audit_service
from app.core.users import CurrentPerson, require
from app.shared.operations import op

router = APIRouter()


@router.get(
    "/api/audit/events",
    response_model=list[AuditEventOut],
    **op("audit.event.list", "List the most recent saved changes, newest first."),
)
async def list_events(
    _person: Annotated[CurrentPerson, require("audit.view")],
    service: Annotated[AuditService, Depends(get_audit_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AuditEventOut]:
    return [
        AuditEventOut(
            id=str(entry.id),
            occurred_at=entry.occurred_at.isoformat(),
            actor_id=str(entry.actor_id),
            acting_as_agent=entry.acting_as_agent,
            operation=entry.operation,
            target=entry.target,
            summary=entry.summary,
            confirmation_id=(
                str(entry.confirmation_id) if entry.confirmation_id is not None else None
            ),
            confirmed_at=(
                entry.confirmed_at.isoformat() if entry.confirmed_at is not None else None
            ),
        )
        for entry in await service.recent(limit)
    ]
