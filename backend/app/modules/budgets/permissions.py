"""Permissions of the budgets module, and the roles it grants them to."""

from app.core.users import ADMIN, FINANCE, MANAGER
from app.shared.permissions import Permission, Role

# Managers see their own department's budgets. Finance and admin see and set every budget.
ROLES: tuple[Role, ...] = (
    Role(MANAGER, frozenset({"budgets.view"})),
    Role(FINANCE, frozenset({"budgets.view", "budgets.manage"})),
    Role(ADMIN, frozenset({"budgets.view", "budgets.manage"})),
)

# Every permission a role names must be declared here, or the app refuses to start.
PERMISSIONS: tuple[Permission, ...] = (
    Permission("budgets.view", agent_allowed=True),
    Permission("budgets.manage", agent_allowed=True),
)
