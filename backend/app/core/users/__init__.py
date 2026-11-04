"""Public interface of the users core module. Other modules import from here only."""

from app.core.users.access import SESSION_COOKIE, advertise_permissions, current_person, require
from app.core.users.api import router
from app.core.users.permissions import (
    ADMIN,
    EMPLOYEE,
    FINANCE,
    KNOWLEDGE_ADMIN,
    MANAGER,
    PERMISSIONS,
    ROLE_NAMES,
    ROLES,
)
from app.core.users.service import CurrentPerson, DepartmentView, UserService, get_user_service

__all__ = [
    "ADMIN",
    "EMPLOYEE",
    "FINANCE",
    "KNOWLEDGE_ADMIN",
    "MANAGER",
    "PERMISSIONS",
    "ROLES",
    "ROLE_NAMES",
    "SESSION_COOKIE",
    "CurrentPerson",
    "DepartmentView",
    "UserService",
    "advertise_permissions",
    "current_person",
    "get_user_service",
    "require",
    "router",
]
