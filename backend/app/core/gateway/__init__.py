"""Public interface of the gateway core module."""

from app.core.gateway.api import router
from app.core.gateway.permissions import PERMISSIONS, ROLES
from app.core.gateway.service import get_upstream_transport

__all__ = ["PERMISSIONS", "ROLES", "get_upstream_transport", "router"]
