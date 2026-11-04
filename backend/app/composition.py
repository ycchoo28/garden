"""The one place that lists installed modules. `just new-module` adds new ones here."""

from dataclasses import dataclass

from fastapi import APIRouter
from sqlalchemy import MetaData

from app.core import audit, confirmations, files, users
from app.core.audit.models import Base as audit_base
from app.core.confirmations.models import Base as confirmations_base
from app.core.files.models import Base as files_base
from app.core.users.models import Base as users_base
from app.modules import knowledge
from app.modules.knowledge.models import Base as knowledge_base
from app.shared.permissions import Permission, Role

# new-module: insert imports above this line


@dataclass(frozen=True)
class InstalledModule:
    name: str
    router: APIRouter
    roles: tuple[Role, ...]
    permissions: tuple[Permission, ...]
    metadata: MetaData


MODULES: tuple[InstalledModule, ...] = (
    InstalledModule("users", users.router, users.ROLES, users.PERMISSIONS, users_base.metadata),
    InstalledModule("audit", audit.router, audit.ROLES, audit.PERMISSIONS, audit_base.metadata),
    InstalledModule("files", files.router, files.ROLES, files.PERMISSIONS, files_base.metadata),
    InstalledModule(
        "confirmations",
        confirmations.router,
        confirmations.ROLES,
        confirmations.PERMISSIONS,
        confirmations_base.metadata,
    ),
    InstalledModule(
        "knowledge",
        knowledge.router,
        knowledge.ROLES,
        knowledge.PERMISSIONS,
        knowledge_base.metadata,
    ),
    # new-module: insert entries above this line
)
