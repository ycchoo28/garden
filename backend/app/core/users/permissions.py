"""The role catalogue. Modules grant their permissions to these roles; startup checks every name.

Roles are granted to people in the users schema. Each signed-in person also holds `employee`.
"""

from app.shared.permissions import Permission, Role

EMPLOYEE = "employee"
MANAGER = "manager"
FINANCE = "finance"
ADMIN = "admin"

# Every role a person can hold. A module may grant permissions only to these names.
ROLE_NAMES: tuple[str, ...] = (EMPLOYEE, MANAGER, FINANCE, ADMIN)

# What the users core grants. Each other module grants its own permissions in its ROLES, and grants
# `admin` every permission that any other role holds, so `admin` is the widest role.
ROLES: tuple[Role, ...] = (
    Role(
        EMPLOYEE,
        frozenset(
            {
                "users.self",
                "files.upload",
                "files.view",
                "files.delete",
                "agent.logins.view",
                "agent.logins.create",
                "agent.logins.revoke",
                "agent.confirmations.decide",
                "agent.model.use",
                "agent.model_usage.view",
            }
        ),
    ),
    Role(MANAGER, frozenset({"users.directory.view"})),
    Role(FINANCE, frozenset({"users.directory.view", "audit.view"})),
    Role(
        ADMIN,
        frozenset(
            {
                "users.self",
                "users.directory.view",
                "audit.view",
            }
        ),
    ),
)

PERMISSIONS: tuple[Permission, ...] = (
    Permission("users.self", agent_allowed=True),
    Permission("users.directory.view", agent_allowed=True),
)
