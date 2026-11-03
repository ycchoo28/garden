"""HTTP routes of the confirmations module: an agent reads its request, the browser decides it."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.core.confirmations.service import (
    ConfirmationService,
    ConfirmationView,
    get_confirmation_service,
)
from app.core.users import CurrentPerson, require
from app.shared.operations import op

router = APIRouter()


class ConfirmationOut(BaseModel):
    id: UUID
    operation: str
    status: str
    expires_at: str

    @classmethod
    def of(cls, view: ConfirmationView) -> "ConfirmationOut":
        return cls(
            id=view.id,
            operation=view.operation,
            status=view.status.value,
            expires_at=view.expires_at.isoformat(),
        )


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]


@router.get(
    "/api/confirmations/{confirmation_id}",
    response_model=ConfirmationOut,
    **op("confirmations.request.read", "Check whether the person has approved a confirmation."),
)
async def read_confirmation(
    confirmation_id: UUID,
    person: Annotated[CurrentPerson, require("users.self")],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ConfirmationOut:
    return ConfirmationOut.of(
        await service.view(confirmation_id=confirmation_id, person_id=person.id)
    )


@router.post(
    "/api/confirmations/{confirmation_id}/decision",
    response_model=ConfirmationOut,
    **op(
        "confirmations.request.decide",
        "Approve or reject a pending confirmation. Only a browser sign-in may do this.",
        tool=False,
    ),
)
async def decide_confirmation(
    confirmation_id: UUID,
    body: DecisionIn,
    person: Annotated[CurrentPerson, require("confirmations.decide")],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ConfirmationOut:
    view = await service.decide(
        confirmation_id=confirmation_id,
        person_id=person.id,
        approve=body.decision == "approve",
    )
    return ConfirmationOut.of(view)
