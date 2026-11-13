"""Permissions the agent module declares. The employee role is granted in users, and admin here.

Only a browser sign-in may decide a confirmation or revoke a CLI login.
"""

from app.core.users import ADMIN
from app.shared.permissions import Permission, Role

PERMISSIONS: tuple[Permission, ...] = (
    Permission("agent.logins.view", agent_allowed=True),
    # Person-only: CLI logins cannot create more CLI logins.
    Permission("agent.logins.create", agent_allowed=False),
    # Person-only: a CLI login never holds it, so an agent cannot revoke its person's logins.
    Permission("agent.logins.revoke", agent_allowed=False),
    # Person-only: a CLI login never holds it, so an agent cannot approve its own request.
    Permission("agent.confirmations.decide", agent_allowed=False),
    Permission("agent.model.use", agent_allowed=True),
    Permission("agent.model_usage.view", agent_allowed=True),
)
# Admin holds every permission another role holds, so it may do the same as employee.
ROLES: tuple[Role, ...] = (
    Role(
        ADMIN,
        frozenset(
            {
                "agent.logins.view",
                "agent.logins.create",
                "agent.logins.revoke",
                "agent.confirmations.decide",
                "agent.model.use",
                "agent.model_usage.view",
            }
        ),
    ),
)
