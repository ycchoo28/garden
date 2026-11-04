"""Public interface of the approvals core module. Other modules import from here only.

Approval flows are data (see models.py). A module starts a run for its subject, reads the run's
state, and records its own audit row for each change it makes. The module owns the subject and
decides what a decision means for it.
"""

from fastapi import APIRouter

from app.core.approvals.models import Flow, FlowStep
from app.core.approvals.service import (
    ApprovalService,
    DecisionView,
    RunView,
    get_approval_service,
)
from app.core.approvals.workflow import Approver, Outcome, RunState, Step
from app.shared.permissions import Permission, Role

# The approvals core has no routes of its own. Owning modules expose the decisions they need.
router = APIRouter()
ROLES: tuple[Role, ...] = ()
PERMISSIONS: tuple[Permission, ...] = ()

__all__ = [
    "PERMISSIONS",
    "ROLES",
    "ApprovalService",
    "Approver",
    "DecisionView",
    "Flow",
    "FlowStep",
    "Outcome",
    "RunState",
    "RunView",
    "Step",
    "get_approval_service",
    "router",
]
