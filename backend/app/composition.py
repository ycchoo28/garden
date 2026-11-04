"""The one place that lists installed modules. `just new-module` adds new ones here."""

from dataclasses import dataclass

from fastapi import APIRouter
from sqlalchemy import MetaData

from app.core import audit, confirmations, files, users
from app.core.audit.models import Base as audit_base
from app.core.confirmations.models import Base as confirmations_base
from app.core.files.models import Base as files_base
from app.core.users.models import Base as users_base
from app.modules import budgets, spend
from app.modules.budgets.models import Base as budgets_base
from app.modules.spend.models import Base as spend_base
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
        "budgets", budgets.router, budgets.ROLES, budgets.PERMISSIONS, budgets_base.metadata
    ),
    InstalledModule("spend", spend.router, spend.ROLES, spend.PERMISSIONS, spend_base.metadata),
    # new-module: insert entries above this line
)
