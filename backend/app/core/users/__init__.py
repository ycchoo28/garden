"""Public interface of the users core module. Other modules import from here only."""

from app.core.users.access import SESSION_COOKIE, advertise_permissions, current_person, require
from app.core.users.api import router
from app.core.users.models import CliLogin
from app.core.users.permissions import (
    ADMIN,
    EMPLOYEE,
    FINANCE,
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
    "MANAGER",
    "PERMISSIONS",
    "ROLES",
    "ROLE_NAMES",
    "SESSION_COOKIE",
    "CliLogin",
    "CurrentPerson",
    "DepartmentView",
    "UserService",
    "advertise_permissions",
    "current_person",
    "get_user_service",
    "require",
    "router",
]
