"""HTTP endpoints for drafting claims: create, read, add and void lines, submit and withdraw.

Submit is person-only. It is not an agent tool, and an agent login never holds its permission.
Withdraw is an agent tool that waits for the person's confirmation.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.core.agent import confirm_agent_action
from app.core.users import CurrentPerson, require
from app.modules.claims.drafting.schemas import AddLineIn, CreateClaimIn, VoidLineIn
from app.modules.claims.drafting.service import ClaimsService, get_claims_service
from app.modules.claims.schemas import ClaimDetailOut, ClaimSummaryOut, VersionIn
from app.shared.operations import op

router = APIRouter()

WITHDRAW_OPERATION = "claims.claim.withdraw"


@router.get(
    "",
    response_model=list[ClaimSummaryOut],
    **op(
        "claims.claim.list",
        "List the claims the caller may see: their own, and their department's if they manage it. "
        "Finance and admin see every claim.",
    ),
)
async def list_claims(
    person: Annotated[CurrentPerson, require("claims.view")],
    service: Annotated[ClaimsService, Depends(get_claims_service)],
) -> list[ClaimSummaryOut]:
    return [ClaimSummaryOut.of(item) for item in await service.list_visible(person)]


@router.post(
    "",
    response_model=ClaimDetailOut,
    status_code=201,
    **op("claims.claim.create", "Create a draft claim. Add lines to it before you submit."),
)
async def create_claim(
    body: CreateClaimIn,
    person: Annotated[CurrentPerson, require("claims.create")],
    service: Annotated[ClaimsService, Depends(get_claims_service)],
) -> ClaimDetailOut:
    return ClaimDetailOut.of(await service.create(person, body.title))


@router.get(
    "/{claim_id}",
    response_model=ClaimDetailOut,
    **op("claims.claim.read", "Read one claim with its lines, receipts and decision history."),
)
async def read_claim(
    claim_id: uuid.UUID,
    person: Annotated[CurrentPerson, require("claims.view")],
    service: Annotated[ClaimsService, Depends(get_claims_service)],
) -> ClaimDetailOut:
    return ClaimDetailOut.of(await service.detail(person, claim_id))


@router.post(
    "/{claim_id}/lines",
    response_model=ClaimDetailOut,
    status_code=201,
    **op(
        "claims.line.add",
        "Add a categorised line to a draft or rejected claim. Attach the receipt file first, "
        "then pass its SHA-256 as receipt_sha256.",
    ),
)
async def add_line(
    claim_id: uuid.UUID,
    body: AddLineIn,
    person: Annotated[CurrentPerson, require("claims.create")],
    service: Annotated[ClaimsService, Depends(get_claims_service)],
) -> ClaimDetailOut:
    detail = await service.add_line(
        person,
        claim_id,
        expected_version=body.expected_version,
        category=body.category,
        spent_on=body.spent_on,
        amount=body.amount,
        description=body.description,
        receipt_sha256=body.receipt_sha256,
    )
    return ClaimDetailOut.of(detail)


@router.post(
    "/{claim_id}/lines/{line_id}/void",
    response_model=ClaimDetailOut,
    **op(
        "claims.line.void",
        "Void a line on a draft or rejected claim. The line stays in the history.",
    ),
)
async def void_line(
    claim_id: uuid.UUID,
    line_id: uuid.UUID,
    body: VoidLineIn,
    person: Annotated[CurrentPerson, require("claims.create")],
    service: Annotated[ClaimsService, Depends(get_claims_service)],
) -> ClaimDetailOut:
    detail = await service.void_line(
        person,
        claim_id,
        line_id,
        expected_version=body.expected_version,
        reason=body.reason,
    )
    return ClaimDetailOut.of(detail)


@router.post(
    "/{claim_id}/submit",
    response_model=ClaimDetailOut,
    **op(
        "claims.claim.submit",
        "Certify and submit a claim for approval. Person-only: an agent cannot submit a claim.",
        tool=False,
    ),
)
async def submit_claim(
    claim_id: uuid.UUID,
    body: VersionIn,
    person: Annotated[CurrentPerson, require("claims.submit")],
    service: Annotated[ClaimsService, Depends(get_claims_service)],
) -> ClaimDetailOut:
    return ClaimDetailOut.of(
        await service.submit(person, claim_id, expected_version=body.expected_version)
    )


@router.post(
    "/{claim_id}/withdraw",
    response_model=ClaimDetailOut,
    dependencies=[confirm_agent_action(WITHDRAW_OPERATION)],
    **op(
        WITHDRAW_OPERATION,
        "Withdraw a submitted claim before anyone has decided it. The person confirms this first.",
        confirm=True,
    ),
)
async def withdraw_claim(
    claim_id: uuid.UUID,
    body: VersionIn,
    person: Annotated[CurrentPerson, require("claims.withdraw")],
    service: Annotated[ClaimsService, Depends(get_claims_service)],
) -> ClaimDetailOut:
    return ClaimDetailOut.of(
        await service.withdraw(person, claim_id, expected_version=body.expected_version)
    )
