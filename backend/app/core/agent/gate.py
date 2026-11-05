"""Route dependency that puts a critical route behind the gate. Browser requests pass through."""

from dataclasses import replace
from typing import Annotated

from fastapi import Depends, Request, params

from app.core.agent.service import ConfirmationService, get_confirmation_service
from app.core.users import CurrentPerson, current_person
from app.shared.request_login import VIA_AGENT, current_request_login

CONFIRMATION_HEADER = "X-Garden-Confirmation"


def confirm_agent_action(operation: str) -> params.Depends:
    """Route dependency. An agent must send an approved confirmation id with the same request."""

    async def gate(
        request: Request,
        person: Annotated[CurrentPerson, Depends(current_person)],
        service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
    ) -> None:
        login = current_request_login.get()
        if login is None or login.via != VIA_AGENT:
            return
        approved = await service.require_approval(
            person_id=person.id,
            login=login,
            operation=operation,
            method=request.method,
            target=request.url.path,
            body=await request.body(),
            confirmation_id=request.headers.get(CONFIRMATION_HEADER),
        )
        # The audit row for the change names the approval that let it run.
        _ = current_request_login.set(
            replace(
                login,
                confirmation_id=str(approved.id),
                confirmed_at=approved.decided_at,
            )
        )

    return Depends(gate)
