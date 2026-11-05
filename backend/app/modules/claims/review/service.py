"""Claim review rules: the queue of claims a person may decide, and the approve or reject decision.

Endpoints call this, never the database. The claim is read and versioned through the drafting
service, which owns it, so review never imports the drafting repository.
"""

import uuid
from typing import Annotated

from fastapi import Depends

from app.core.approvals import ApprovalService, Outcome, RunState, get_approval_service
from app.core.audit import AuditService, get_audit_service
from app.core.users import CurrentPerson
from app.modules.claims.drafting.service import ClaimsService, get_claims_service
from app.modules.claims.flows import SUBJECT_TYPE
from app.modules.claims.views import ClaimDetail, ClaimSummary, approver_of
from app.shared.errors import Conflict, Forbidden


class ReviewService:
    def __init__(
        self,
        claims: ClaimsService,
        approvals: ApprovalService,
        audit: AuditService,
    ) -> None:
        self._claims = claims
        self._approvals = approvals
        self._audit = audit

    async def queue(self, caller: CurrentPerson) -> list[ClaimSummary]:
        """Claims waiting for a decision that this person may make now."""
        waiting = await self._approvals.pending_for(approver_of(caller), SUBJECT_TYPE)
        return await self._claims.summaries_by_ids([view.subject_id for view in waiting])

    async def decide(
        self,
        caller: CurrentPerson,
        claim_id: uuid.UUID,
        *,
        expected_version: int,
        outcome: Outcome,
        reason: str | None,
    ) -> ClaimDetail:
        claim = await self._claims.visible_claim(caller, claim_id)
        if claim.claimant_id == caller.id:
            raise Forbidden("Nobody can approve or reject their own claim.")
        claim = await self._claims.bump(claim.id, expected_version)
        latest = (await self._approvals.latest(SUBJECT_TYPE, [claim.id])).get(claim.id)
        if latest is None or latest.current is None or latest.state is not RunState.PENDING:
            raise Conflict("This claim is not waiting for a decision.")
        step_role = latest.current.role
        _ = await self._approvals.decide(
            run_id=latest.id,
            approver=approver_of(caller),
            approver_name=caller.name,
            outcome=outcome,
            reason=reason,
        )
        self._audit.record(
            actor_id=caller.id,
            operation="claims.claim.decide",
            target=f"claim:{claim.id}",
            summary=f"{outcome.value.capitalize()} the claim as {step_role}.",
        )
        await self._claims.commit()
        return await self._claims.detail(caller, claim.id)


def get_review_service(
    claims: Annotated[ClaimsService, Depends(get_claims_service)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> ReviewService:
    return ReviewService(claims, approvals, audit)
