"""Permissions the gateway module declares. The employee role grants them."""

from app.shared.permissions import Permission

PERMISSIONS = (
    Permission("gateway.model.use", agent_allowed=True),
    Permission("gateway.usage.view", agent_allowed=True),
)
ROLES = ()
