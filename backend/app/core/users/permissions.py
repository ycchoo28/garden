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
                "confirmations.decide",
                "claims.view",
                "claims.create",
                "claims.submit",
                "claims.withdraw",
            }
        ),
    ),
    Role(
        MANAGER,
        frozenset({"users.directory.view", "files.view", "claims.decide"}),
    ),
    Role(
        FINANCE,
        frozenset({"users.directory.view", "audit.view", "files.view", "claims.decide"}),
    ),
    # Knowledge permissions arrive with the knowledge module; until then this role grants nothing.
    Role(KNOWLEDGE_ADMIN, frozenset()),
    Role(ADMIN, frozenset({"users.directory.view", "audit.view", "files.view"})),
)

PERMISSIONS: tuple[Permission, ...] = (
    Permission("users.self", agent_allowed=True),
    Permission("users.directory.view", agent_allowed=True),
)
