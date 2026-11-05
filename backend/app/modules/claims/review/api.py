"""HTTP endpoints for reviewing claims: the approval queue, and approve or reject.

Approve and reject are person-only. They are not agent tools, and an agent login never holds
their permissions.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.users import CurrentPerson, require
from app.modules.claims.review.schemas import ClaimDecisionIn, outcome_of
from app.modules.claims.review.service import ReviewService, get_review_service
from app.modules.claims.schemas import ClaimDetailOut, ClaimSummaryOut
from app.shared.operations import op

router = APIRouter()


@router.get(
    "/queue",
    response_model=list[ClaimSummaryOut],
    **op(
        "claims.decision.queue",
        "List the claims waiting for a decision that the caller may make. Person-only.",
        tool=False,
    ),
)
async def approval_queue(
    person: Annotated[CurrentPerson, require("claims.decide")],
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> list[ClaimSummaryOut]:
    return [ClaimSummaryOut.of(item) for item in await service.queue(person)]


@router.post(
    "/{claim_id}/decision",
    response_model=ClaimDetailOut,
    **op(
        "claims.claim.decide",
        "Approve or reject the claim's current step. A rejection needs a reason. "
        "Person-only: an agent cannot approve or reject a claim.",
        tool=False,
    ),
)
async def decide_claim(
    claim_id: uuid.UUID,
    body: ClaimDecisionIn,
    person: Annotated[CurrentPerson, require("claims.decide")],
    service: Annotated[ReviewService, Depends(get_review_service)],
) -> ClaimDetailOut:
    detail = await service.decide(
        person,
        claim_id,
        expected_version=body.expected_version,
        outcome=outcome_of(body.decision),
        reason=body.reason,
    )
    return ClaimDetailOut.of(detail)
