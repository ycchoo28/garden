"""Public interface of the files core module."""

from app.core.files.api import router
from app.core.files.permissions import PERMISSIONS, ROLES
from app.core.files.service import FileService, get_file_service

__all__ = ["PERMISSIONS", "ROLES", "FileService", "get_file_service", "router"]
