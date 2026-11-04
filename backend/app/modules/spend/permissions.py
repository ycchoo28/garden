"""Permissions of the spend module. The roles that grant them are in the users module."""

from app.shared.permissions import Permission, Role

# Finance and admin hold these. Their grants sit with the other role grants in the users module.
ROLES: tuple[Role, ...] = ()

# Every permission a role names must be declared here, or the app refuses to start.
# Agents may hold all four. Void and month close also need the person's confirmation.
PERMISSIONS: tuple[Permission, ...] = (
    Permission("spend.statement.view", agent_allowed=True),
    Permission("spend.statement.import", agent_allowed=True),
    Permission("spend.statement.void", agent_allowed=True),
    Permission("spend.month.close", agent_allowed=True),
)
