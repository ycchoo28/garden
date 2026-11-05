"""The knowledge batch approval flow, as data. `just seed` loads it into the approvals schema.

One admin must approve a batch, and the person who asked for approval may not, even when that
person is an admin. The step is not department scoped, so any admin may approve any batch. A batch
has no amount, so its one step applies to every run: the run's amount is zero and the step's
threshold is below zero.
"""

from dataclasses import dataclass
from decimal import Decimal

from app.core.users import ADMIN

KNOWLEDGE_FLOW_CODE = "knowledge.batch"
KNOWLEDGE_FLOW_NAME = "Knowledge batch"
SUBJECT_TYPE = "knowledge_batch"
RUN_AMOUNT = Decimal("0.00")


@dataclass(frozen=True)
class KnowledgeFlowStep:
    position: int
    role: str
    department_scoped: bool
    above: Decimal


KNOWLEDGE_FLOW_STEPS: tuple[KnowledgeFlowStep, ...] = (
    KnowledgeFlowStep(position=1, role=ADMIN, department_scoped=False, above=Decimal("-1.00")),
)
