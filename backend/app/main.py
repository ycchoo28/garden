"""Application factory. It wires the shared plumbing and the installed modules together."""

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.composition import MODULES, PORTS, InstalledModule
from app.core.users import ROLE_NAMES, advertise_permissions
from app.shared.contract import install_contract_headers
from app.shared.errors import PROBLEM_RESPONSES, install_problem_handlers
from app.shared.jobs import app as jobs
from app.shared.operations import op
from app.shared.permissions import (
    Permission,
    Role,
    check_roles_declare_permissions,
    merge_roles,
)
from app.shared.tracing import TraceAPIOperations


def _configure_tracing() -> TracerProvider | None:
    if not (
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    ):
        return None
    provider = TracerProvider(resource=Resource.create({"service.name": "garden-api"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


tracer_provider = _configure_tracing()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
    # The API hands work to the background worker through the job queue, so it opens the queue too.
    try:
        async with jobs.open_async():
            yield
    finally:
        if tracer_provider is not None:
            tracer_provider.force_flush(timeout_millis=5_000)


def create_app(modules: tuple[InstalledModule, ...] = MODULES) -> FastAPI:
    # Each module grants its own permissions to the company's roles; grants to one role combine.
    roles: tuple[Role, ...] = merge_roles(
        (role for installed in modules for role in installed.roles), ROLE_NAMES
    )
    permissions: tuple[Permission, ...] = tuple(
        permission for installed in modules for permission in installed.permissions
    )
    # A role that grants a permission no module declares stops the app before it serves anything.
    check_roles_declare_permissions(roles, permissions)

    app = FastAPI(
        title="Garden",
        responses=PROBLEM_RESPONSES,
        lifespan=lifespan,
        # Under /api, which every portal address forwards to the API, so the CLI can read it.
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(TraceAPIOperations)
    install_problem_handlers(app)
    install_contract_headers(app)
    advertise_permissions(app)

    @app.get(
        "/api/health",
        tags=["platform"],
        **op("platform.health.read", "Say whether Garden is up. Needs no sign-in.", confirm=False),
    )
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    # Every role and permission of every installed module. The access check reads them from here.
    app.state.roles = roles
    app.state.permissions = permissions
    for installed in modules:
        app.include_router(installed.router)
    for placeholder, provider in PORTS:
        app.dependency_overrides[placeholder] = provider
    return app


app = create_app()
