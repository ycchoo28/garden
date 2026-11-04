"""The role catalogue. Roles grant permissions that modules declare; app startup checks every name.

Roles are granted to people in the users schema. Each signed-in person also holds `employee`.
"""

from app.shared.permissions import Permission, Role

EMPLOYEE = "employee"
MANAGER = "manager"
FINANCE = "finance"
KNOWLEDGE_ADMIN = "knowledge_admin"
ADMIN = "admin"

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
            }
        ),
    ),
    Role(MANAGER, frozenset({"users.directory.view"})),
    Role(FINANCE, frozenset({"users.directory.view", "audit.view"})),
    # Knowledge permissions arrive with the knowledge module; until then this role grants nothing.
    Role(KNOWLEDGE_ADMIN, frozenset()),
    Role(ADMIN, frozenset({"users.directory.view", "audit.view"})),
)

PERMISSIONS: tuple[Permission, ...] = (
    Permission("users.self", agent_allowed=True),
    Permission("users.directory.view", agent_allowed=True),
    Permission("cli_logins.view", agent_allowed=True),
    # Person-only: a CLI login never holds it, so an agent cannot revoke the logins of its person.
    Permission("cli_logins.revoke", agent_allowed=False),
)
