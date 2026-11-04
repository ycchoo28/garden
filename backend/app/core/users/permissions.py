"""The role catalogue. Roles grant permissions that modules declare; app startup checks every name.

Roles are granted to people in the users schema. Each signed-in person also holds `employee`.
"""

from app.shared.permissions import Permission, Role

EMPLOYEE = "employee"
MANAGER = "manager"
FINANCE = "finance"
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
                "files.delete",
                "cli_logins.view",
                "cli_logins.revoke",
                "confirmations.decide",
                "gateway.model.use",
                "gateway.usage.view",
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
    Role(
        ADMIN,
        frozenset({"users.directory.view", "audit.view", "files.view"}) | FINANCE_BUDGET_AND_SPEND,
    ),
)

PERMISSIONS: tuple[Permission, ...] = (
    Permission("users.self", agent_allowed=True),
    Permission("users.directory.view", agent_allowed=True),
    Permission("cli_logins.view", agent_allowed=True),
    # Person-only: a CLI login never holds it, so an agent cannot revoke the logins of its person.
    Permission("cli_logins.revoke", agent_allowed=False),
)
