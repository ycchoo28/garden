"""Roles and permissions of the knowledge module, checked by the portal."""

from app.shared.permissions import Permission, Role

KNOWLEDGE_ADMIN = "knowledge_admin"

ROLES: tuple[Role, ...] = (
    Role(
        KNOWLEDGE_ADMIN,
        frozenset(
            {
                "knowledge.collection.view",
                "knowledge.collection.manage",
                "knowledge.batch.prepare",
            }
        ),
    ),
)

# Every permission a role names must be declared here, or the app refuses to start.
# agent_allowed=False marks an act only a person may do, which an agent login never holds.
PERMISSIONS: tuple[Permission, ...] = (
    Permission("knowledge.collection.view", agent_allowed=True),
    Permission("knowledge.collection.manage", agent_allowed=False),
    Permission("knowledge.batch.prepare", agent_allowed=True),
)
