"""The expense claim approval flow, as data. `just seed` loads it into the approvals schema.

A claim goes to its department's manager first. A claim above the finance threshold then needs a
second decision from finance. The threshold is data, so changing it changes the seed, not the code.
"""

from dataclasses import dataclass
from decimal import Decimal

CLAIM_FLOW_CODE = "claims.expense"
CLAIM_FLOW_NAME = "Expense claim"
SUBJECT_TYPE = "claim"
FINANCE_THRESHOLD = Decimal("500.00")


@dataclass(frozen=True)
class FlowStepSeed:
    position: int
    role: str
    department_scoped: bool
    above: Decimal


CLAIM_FLOW_STEPS: tuple[FlowStepSeed, ...] = (
    FlowStepSeed(position=1, role="manager", department_scoped=True, above=Decimal("0.00")),
    FlowStepSeed(position=2, role="finance", department_scoped=False, above=FINANCE_THRESHOLD),
)
