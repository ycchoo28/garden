"""Permissions the claims module declares. The roles that grant them are in the users module.

Certifying, submitting and deciding are person-only: an agent login never holds them.
"""

from app.shared.permissions import Permission, Role

ROLES: tuple[Role, ...] = ()

PERMISSIONS: tuple[Permission, ...] = (
    Permission("claims.view", agent_allowed=True),
    Permission("claims.create", agent_allowed=True),
    Permission("claims.submit", agent_allowed=False),
    Permission("claims.withdraw", agent_allowed=True),
    Permission("claims.decide", agent_allowed=False),
)
