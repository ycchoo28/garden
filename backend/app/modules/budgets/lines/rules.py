"""Pure helpers for budget lines: which departments a caller may read, and how to name one."""

import uuid

from app.core.users import CurrentPerson, DepartmentView


def visible_departments(
    caller: CurrentPerson, department_id: uuid.UUID | None = None
) -> frozenset[uuid.UUID] | None:
    """The departments the caller may see. None means every department."""
    if department_id is not None:
        return frozenset({department_id})
    if caller.scope.all_departments:
        return None
    return caller.scope.department_ids


def department_code(departments: dict[uuid.UUID, DepartmentView], department_id: uuid.UUID) -> str:
    department = departments.get(department_id)
    return department.code if department is not None else "unknown"
