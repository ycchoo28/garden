"""Permissions of the budgets module, and the roles it grants them to."""

from app.core.users import ADMIN, EMPLOYEE, FINANCE, MANAGER
from app.shared.permissions import Permission, Role

# Managers see their own department's budgets. Finance and admin set every budget and close months.
ROLES: tuple[Role, ...] = (
    Role(EMPLOYEE, frozenset({"budgets.category.read"})),
    Role(MANAGER, frozenset({"budgets.view", "budgets.category.read"})),
    Role(
        FINANCE,
        frozenset(
            {"budgets.view", "budgets.manage", "budgets.month.close", "budgets.category.read"}
        ),
    ),
    Role(
        ADMIN,
        frozenset(
            {"budgets.view", "budgets.manage", "budgets.month.close", "budgets.category.read"}
        ),
    ),
)

# Every permission a role names must be declared here, or the app refuses to start.
# Agents may hold all four. Closing a month also needs the person's confirmation.
PERMISSIONS: tuple[Permission, ...] = (
    Permission("budgets.category.read", agent_allowed=True),
    Permission("budgets.view", agent_allowed=True),
    Permission("budgets.manage", agent_allowed=True),
    Permission("budgets.month.close", agent_allowed=True),
)
