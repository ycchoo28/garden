"""Permissions the confirmations module declares. Only a browser sign-in may decide."""

from app.shared.permissions import Permission

# agent_allowed=False: a CLI login never holds this, so an agent cannot approve its own request.
PERMISSIONS = (Permission("confirmations.decide", agent_allowed=False),)
ROLES = ()
