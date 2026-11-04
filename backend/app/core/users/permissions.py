"""The role catalogue. Roles grant permissions that modules declare; app startup checks every name.

Roles are granted to people in the users schema. Each signed-in person also holds `employee`.
"""

from app.shared.permissions import Permission, Role

EMPLOYEE = "employee"
MANAGER = "manager"
FINANCE = "finance"
ADMIN = "admin"

ROLES: tuple[Role, ...] = (
    Role(
        EMPLOYEE,
        frozenset({"users.self", "files.upload", "files.view", "confirmations.decide"}),
    ),
    Role(MANAGER, frozenset({"users.directory.view"})),
    Role(FINANCE, frozenset({"users.directory.view", "audit.view"})),
    Role(ADMIN, frozenset({"users.directory.view", "audit.view"})),
)

PERMISSIONS: tuple[Permission, ...] = (
    Permission("users.self", agent_allowed=True),
    Permission("users.directory.view", agent_allowed=True),
)
