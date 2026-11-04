"""Application factory. It wires the shared plumbing and the installed modules together."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.composition import MODULES, InstalledModule
from app.core.users import advertise_permissions
from app.shared.contract import install_contract_headers
from app.shared.errors import PROBLEM_RESPONSES, install_problem_handlers
from app.shared.jobs import app as jobs
from app.shared.operations import op
from app.shared.permissions import Permission, Role, check_roles_declare_permissions


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # The API hands work to the background worker through the job queue, so it opens the queue too.
    async with jobs.open_async():
        yield


def create_app(modules: tuple[InstalledModule, ...] = MODULES) -> FastAPI:
    roles: tuple[Role, ...] = tuple(role for installed in modules for role in installed.roles)
    permissions: tuple[Permission, ...] = tuple(
        permission for installed in modules for permission in installed.permissions
    )
    # A role that grants a permission no module declares stops the app before it serves anything.
    check_roles_declare_permissions(roles, permissions)

    app = FastAPI(title="Garden", responses=PROBLEM_RESPONSES, lifespan=lifespan)
    install_problem_handlers(app)
    install_contract_headers(app)
    advertise_permissions(app)

    @app.get(
        "/api/health",
        tags=["platform"],
        **op("platform.health.read", "Say whether Garden is up. Needs no sign-in."),
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Every role and permission of every installed module. The access check reads them from here.
    app.state.roles = roles
    app.state.permissions = permissions
    for installed in modules:
        app.include_router(installed.router)
    return app


app = create_app()
