"""Permissions of the budgets module. The roles that grant them are in the users module."""

from app.shared.permissions import Permission, Role

# Managers hold budgets.view, and finance and admin hold both. The grants sit in the users module.
ROLES: tuple[Role, ...] = ()

# Every permission a role names must be declared here, or the app refuses to start.
PERMISSIONS: tuple[Permission, ...] = (
    Permission("budgets.view", agent_allowed=True),
    Permission("budgets.manage", agent_allowed=True),
)
