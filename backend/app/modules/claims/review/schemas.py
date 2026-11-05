"""Request shape for deciding a claim: approve or reject, with an optional reason."""

from typing import Literal

from pydantic import Field

from app.core.approvals import Outcome
from app.modules.claims.schemas import VersionIn


class ClaimDecisionIn(VersionIn):
    decision: Literal["approve", "reject"]
    reason: str | None = Field(default=None, max_length=500)


def outcome_of(decision: Literal["approve", "reject"]) -> Outcome:
    return Outcome.APPROVED if decision == "approve" else Outcome.REJECTED
