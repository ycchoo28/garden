"""Permissions the files module declares. The employee role grants them."""

from app.core.users import ADMIN
from app.shared.permissions import Permission, Role

PERMISSIONS = (
    Permission("files.upload", agent_allowed=True),
    Permission("files.view", agent_allowed=True),
    Permission("files.delete", agent_allowed=True),
)
# Admin holds every permission another role holds, so it grants the same as employee.
ROLES: tuple[Role, ...] = (Role(ADMIN, frozenset({"files.upload", "files.view", "files.delete"})),)
