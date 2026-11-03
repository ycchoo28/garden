"""Permissions the files module declares. The employee role grants them."""

from app.shared.permissions import Permission

PERMISSIONS = (
    Permission("files.upload", agent_allowed=True),
    Permission("files.view", agent_allowed=True),
)
ROLES = ()
