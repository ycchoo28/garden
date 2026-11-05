"""Roles and permissions of the knowledge module, checked by the portal."""

from app.core.users import ADMIN, EMPLOYEE, FINANCE
from app.shared.permissions import Permission, Role

# Every signed-in person holds `employee`, so every person may search. Collection rules then decide
# which documents the search may return.
# Finance starts batches (uploads the expense policies) but cannot decide them: the admin approves.
# Admin manages collections, decides batches and erases documents.
ROLES: tuple[Role, ...] = (
    Role(EMPLOYEE, frozenset({"knowledge.search"})),
    Role(
        FINANCE,
        frozenset(
            {
                "knowledge.collection.view",
                "knowledge.batch.prepare",
                "knowledge.batch.submit",
                "knowledge.batch.cancel",
            }
        ),
    ),
    Role(
        ADMIN,
        frozenset(
            {
                "knowledge.search",
                "knowledge.collection.view",
                "knowledge.collection.manage",
                "knowledge.batch.prepare",
                "knowledge.batch.submit",
                "knowledge.batch.decide",
                "knowledge.batch.cancel",
                "knowledge.batch.undo",
                "knowledge.document.erase",
                "knowledge.search_log.read",
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
    # Asking for approval and cancelling a request are agent-allowed. Deciding is not.
    Permission("knowledge.batch.submit", agent_allowed=True),
    Permission("knowledge.batch.decide", agent_allowed=False),
    Permission("knowledge.batch.cancel", agent_allowed=True),
    # Undo is agent-allowed, but the route waits for the person's confirmation.
    Permission("knowledge.batch.undo", agent_allowed=True),
    Permission("knowledge.document.erase", agent_allowed=False),
    Permission("knowledge.search", agent_allowed=True),
    Permission("knowledge.search_log.read", agent_allowed=False),
)
