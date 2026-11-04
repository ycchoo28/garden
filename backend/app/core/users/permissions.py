"""The role catalogue. Roles grant permissions that modules declare; app startup checks every name.

Roles are granted to people in the users schema. Each signed-in person also holds `employee`.
"""

from app.shared.permissions import Permission, Role

EMPLOYEE = "employee"
MANAGER = "manager"
FINANCE = "finance"
KNOWLEDGE_ADMIN = "knowledge_admin"
ADMIN = "admin"

# What finance and admin hold over budgets and card statements. The business modules declare them.
# Role grants merge by name across modules, so the business grants are listed beside the others.
FINANCE_BUDGET_AND_SPEND = frozenset(
    {
        "budgets.view",
        "budgets.manage",
        "spend.statement.view",
        "spend.statement.import",
        "spend.statement.void",
        "spend.month.close",
    }
)

ROLES: tuple[Role, ...] = (
    Role(
        EMPLOYEE,
        frozenset(
            {
                "users.self",
                "files.upload",
                "files.view",
                "confirmations.decide",
                "claims.view",
                "claims.create",
                "claims.submit",
                "claims.withdraw",
            }
        ),
    ),
    Role(
        MANAGER, frozenset({"users.directory.view", "files.view", "claims.decide", "budgets.view"})
    ),
    Role(
        FINANCE,
        frozenset({"users.directory.view", "audit.view", "files.view", "claims.decide"})
        | FINANCE_BUDGET_AND_SPEND,
    ),
    # Knowledge permissions arrive with the knowledge module; until then this role grants nothing.
    Role(KNOWLEDGE_ADMIN, frozenset()),
    Role(
        ADMIN,
        frozenset({"users.directory.view", "audit.view", "files.view"}) | FINANCE_BUDGET_AND_SPEND,
    ),
)

PERMISSIONS: tuple[Permission, ...] = (
    Permission("users.self", agent_allowed=True),
    Permission("users.directory.view", agent_allowed=True),
)
