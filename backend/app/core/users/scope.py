"""The data scope of a person: which departments' records they may see.

Managers see their own department. Finance and admin see every department. Everyone else sees none
beyond their own record. Business modules filter their queries with `DataScope.department_ids`.
"""

import uuid
from dataclasses import dataclass

from app.core.users.permissions import ADMIN, FINANCE, MANAGER


@dataclass(frozen=True)
class DataScope:
    all_departments: bool
    department_ids: frozenset[uuid.UUID]

    @classmethod
    def for_roles(cls, roles: frozenset[str], own_department: uuid.UUID) -> "DataScope":
        if roles & {FINANCE, ADMIN}:
            return cls(all_departments=True, department_ids=frozenset())
        if MANAGER in roles:
            return cls(all_departments=False, department_ids=frozenset({own_department}))
        return cls(all_departments=False, department_ids=frozenset())

    def covers(self, department_id: uuid.UUID) -> bool:
        return self.all_departments or department_id in self.department_ids
