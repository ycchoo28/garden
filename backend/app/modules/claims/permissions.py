"""Permissions the claims module declares, and the roles it grants them to.

Certifying, submitting and deciding are person-only: an agent login never holds them.
"""

from app.core.users import ADMIN, EMPLOYEE, FINANCE, MANAGER
from app.shared.permissions import Permission, Role

ROLES: tuple[Role, ...] = (
    Role(EMPLOYEE, frozenset({"claims.view", "claims.create", "claims.submit", "claims.withdraw"})),
    Role(MANAGER, frozenset({"claims.decide"})),
    Role(FINANCE, frozenset({"claims.decide"})),
    Role(
        ADMIN,
        frozenset(
            {"claims.view", "claims.create", "claims.submit", "claims.withdraw", "claims.decide"}
        ),
    ),
)

PERMISSIONS: tuple[Permission, ...] = (
    Permission("claims.view", agent_allowed=True),
    Permission("claims.create", agent_allowed=True),
    Permission("claims.submit", agent_allowed=False),
    Permission("claims.withdraw", agent_allowed=True),
    Permission("claims.decide", agent_allowed=False),
)
