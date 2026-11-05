"""Public interface of the agent core module: CLI logins, the confirmation gate that critical routes
declare, and the model gateway.

Other modules and the app import only from this file, never from its submodules.
"""

from app.core.agent.api import router
from app.core.agent.gate import CONFIRMATION_HEADER, confirm_agent_action
from app.core.agent.model_service import get_upstream_transport
from app.core.agent.permissions import PERMISSIONS, ROLES

__all__ = [
    "CONFIRMATION_HEADER",
    "PERMISSIONS",
    "ROLES",
    "confirm_agent_action",
    "get_upstream_transport",
    "router",
]
