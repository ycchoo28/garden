"""Access-rule rules that need no database: check the roles, store the rules, and read them back.

Collections and batches both use these, so they live at the module root and not in one feature.
"""

from collections.abc import Sequence
from typing import Any

from app.modules.knowledge.schemas import (
    ASSIGNABLE_ROLES,
    CollectionRuleIn,
    CollectionRuleOut,
)
from app.shared.errors import Unprocessable


def rule_specs(rules: Sequence[CollectionRuleIn]) -> list[dict[str, Any]]:
    unknown = sorted({rule.role for rule in rules} - ASSIGNABLE_ROLES)
    if unknown:
        raise Unprocessable(f"Unknown role(s): {', '.join(unknown)}.")
    return [
        {
            "role": rule.role,
            "all_departments": rule.all_departments,
            "department_codes": sorted({code.strip().lower() for code in rule.department_codes}),
        }
        for rule in rules
    ]


def rule_out(spec: dict[str, Any]) -> CollectionRuleOut:
    return CollectionRuleOut(
        role=spec["role"],
        all_departments=spec["all_departments"],
        department_codes=list(spec["department_codes"]),
    )
