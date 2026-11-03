"""Permissions the audit module declares. The roles that grant them are in the users module."""

from app.shared.permissions import Permission

PERMISSIONS = (Permission("audit.view", agent_allowed=True),)
ROLES = ()
