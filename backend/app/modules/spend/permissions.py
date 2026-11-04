"""Permissions of the spend module, and the roles it grants them to."""

from app.core.users import ADMIN, FINANCE
from app.shared.permissions import Permission, Role

# Finance and admin hold every spend permission.
_FINANCE_SPEND = frozenset(
    {
        "spend.statement.view",
        "spend.statement.import",
        "spend.statement.void",
        "spend.month.close",
    }
)

ROLES: tuple[Role, ...] = (Role(FINANCE, _FINANCE_SPEND), Role(ADMIN, _FINANCE_SPEND))

# Every permission a role names must be declared here, or the app refuses to start.
# Agents may hold all four. Void and month close also need the person's confirmation.
PERMISSIONS: tuple[Permission, ...] = (
    Permission("spend.statement.view", agent_allowed=True),
    Permission("spend.statement.import", agent_allowed=True),
    Permission("spend.statement.void", agent_allowed=True),
    Permission("spend.month.close", agent_allowed=True),
)
