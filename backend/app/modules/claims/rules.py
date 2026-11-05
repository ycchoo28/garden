"""The claim rules that need no database: the status and the month of a line.

A claim's status is computed from its latest approval run, so a claim cannot disagree with its
recorded decisions.
"""

from datetime import date
from enum import StrEnum

from app.core.approvals import RunState


class ClaimStatus(StrEnum):
    DRAFT = "draft"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"


def status_of(latest: RunState | None) -> ClaimStatus:
    """A claim with no run is a draft. Otherwise its newest run says where it stands."""
    if latest is None:
        return ClaimStatus.DRAFT
    match latest:
        case RunState.PENDING:
            return ClaimStatus.SUBMITTED
        case RunState.APPROVED:
            return ClaimStatus.APPROVED
        case RunState.REJECTED:
            return ClaimStatus.REJECTED
        case RunState.CANCELLED:
            return ClaimStatus.WITHDRAWN


# A claimant may change lines while the claim is a draft, or after a rejection they want to correct.
EDITABLE: frozenset[ClaimStatus] = frozenset({ClaimStatus.DRAFT, ClaimStatus.REJECTED})
SUBMITTABLE: frozenset[ClaimStatus] = EDITABLE


def month_of(day: date) -> str:
    """The month a line counts toward, as YYYY-MM."""
    return f"{day.year:04d}-{day.month:02d}"
