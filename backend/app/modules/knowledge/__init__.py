"""Public interface of the knowledge module. Other modules import only what is listed here."""

from app.modules.knowledge.api import router
from app.modules.knowledge.permissions import PERMISSIONS, ROLES

__all__ = ["PERMISSIONS", "ROLES", "router"]
