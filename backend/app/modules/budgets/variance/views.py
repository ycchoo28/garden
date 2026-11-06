"""Plain rows returned by the Variance repository and exposed by its service."""

import uuid
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class VarianceRow:
    department_id: uuid.UUID
    department_code: str
    department_name: str
    year_month: str
    category: str
    budget: Decimal | None
    actual: Decimal


@dataclass(frozen=True)
class VarianceView:
    department_id: uuid.UUID
    department_code: str
    department_name: str
    year_month: str
    category: str
    budget: Decimal | None
    actual: Decimal
    variance: Decimal | None
