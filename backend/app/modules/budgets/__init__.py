"""Public interface of the budgets module. Other modules import only what is listed here."""

from app.modules.budgets.api import router
from app.modules.budgets.permissions import PERMISSIONS, ROLES

__all__ = ["PERMISSIONS", "ROLES", "router"]
