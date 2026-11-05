"""The one place that lists installed modules. `just new-module` adds new ones here."""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter
from sqlalchemy import MetaData

from app.core import agent, approvals, audit, files, users
from app.core.agent.models import Base as agent_base
from app.core.approvals.models import Base as approvals_base
from app.core.audit.models import Base as audit_base
from app.core.files.models import Base as files_base
from app.core.users.models import Base as users_base
from app.modules import budgets, claims, knowledge
from app.modules.budgets.models import Base as budgets_base
from app.modules.claims.models import Base as claims_base
from app.modules.claims.ports import get_closed_months as claims_closed_months
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
        "approvals",
        approvals.router,
        approvals.ROLES,
        approvals.PERMISSIONS,
        approvals_base.metadata,
    ),
    InstalledModule("agent", agent.router, agent.ROLES, agent.PERMISSIONS, agent_base.metadata),
    InstalledModule(
        "claims", claims.router, claims.ROLES, claims.PERMISSIONS, claims_base.metadata
    ),
    InstalledModule(
        "budgets", budgets.router, budgets.ROLES, budgets.PERMISSIONS, budgets_base.metadata
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

# A port one module declares and another module implements. The app puts the provider in place of
# the placeholder, so the module that declares the port never imports the one that provides it.
PORTS: tuple[tuple[Callable[..., object], Callable[..., object]], ...] = (
    (claims_closed_months, budgets.get_closed_months_for_claims),
)
