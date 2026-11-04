"""Architecture rules the code must keep: deny by default, contract metadata, module boundaries."""

import uuid
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient

from app.composition import MODULES
from app.core.users import PERMISSIONS as USER_PERMISSIONS
from app.core.users.access import route_permission
from app.main import app
from app.shared.operations import OPERATION_ID_PATTERN
from app.shared.permissions import Permission

BACKEND_ROOT = Path(__file__).resolve().parents[2]

# Routes that answer without a signed-in person. Every other route must name a permission.
PUBLIC_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/auth/login"),
    ("GET", "/auth/callback"),
    ("POST", "/api/users/sign-out"),
    # The CLI starts and finishes its sign-in before it holds a token, so these take no person.
    ("GET", "/api/cli-logins/authorize"),
    ("POST", "/api/cli-logins"),
}

# Routes an agent may only call after the person approves, listed explicitly.
CRITICAL_ROUTES: frozenset[str] = frozenset(
    {
        "/api/claims/{claim_id}/withdraw",
        "/api/spend/statements/{statement_id}/void",
        "/api/spend/months/{year_month}/close",
        "/api/files/{sha256}",
    }
)


def _api_routes() -> list[APIRoute]:
    """Every route of the app: the platform's own, and each installed module's router."""
    routes = [route for route in app.routes if isinstance(route, APIRoute)]
    for installed in MODULES:
        routes.extend(route for route in installed.router.routes if isinstance(route, APIRoute))
    return [route for route in routes if route.include_in_schema]


def _guarded(route: APIRoute) -> bool:
    return route_permission(route) is not None


def test_every_route_declares_agent_metadata() -> None:
    for route in _api_routes():
        extra = route.openapi_extra or {}
        assert "x-agent" in extra, f"{route.path} has no op() metadata"
        assert OPERATION_ID_PATTERN.fullmatch(route.operation_id or ""), route.path


def test_operation_ids_are_unique() -> None:
    ids = [route.operation_id for route in _api_routes()]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


def test_every_route_is_guarded_or_public() -> None:
    for route in _api_routes():
        for method in route.methods:
            if (method, route.path) in PUBLIC_ROUTES:
                continue
            assert _guarded(route), f"{method} {route.path} has no permission check"


def test_public_routes_exist() -> None:
    present = {(method, route.path) for route in _api_routes() for method in route.methods}
    assert present >= PUBLIC_ROUTES


def test_critical_routes_are_listed() -> None:
    for route in _api_routes():
        confirm = (route.openapi_extra or {}).get("x-agent", {}).get("confirm", False)
        if confirm:
            assert route.path in CRITICAL_ROUTES, f"{route.path} is critical but not listed"


def test_agent_only_permissions_exclude_browser_acts() -> None:
    declared: dict[str, Permission] = {
        permission.name: permission for installed in MODULES for permission in installed.permissions
    }
    assert declared["confirmations.decide"].agent_allowed is False
    assert declared["cli_logins.revoke"].agent_allowed is False
    assert declared["users.self"].agent_allowed is True
    assert USER_PERMISSIONS


def test_module_names_are_unique_and_match_their_schemas() -> None:
    names = [installed.name for installed in MODULES]
    assert len(names) == len(set(names))
    for installed in MODULES:
        schemas = {table.schema for table in installed.metadata.sorted_tables}
        assert schemas <= {installed.name}, installed.name


def test_no_cross_schema_foreign_keys() -> None:
    for installed in MODULES:
        for table in installed.metadata.sorted_tables:
            for foreign_key in table.foreign_keys:
                target_schema = foreign_key.column.table.schema
                assert target_schema == table.schema, f"{table.fullname} references {target_schema}"


def test_each_migration_branch_is_a_module() -> None:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    labels = {
        label for revision in script.walk_revisions() for label in (revision.branch_labels or ())
    }
    assert labels == {installed.name for installed in MODULES}


def test_identifiers_are_uuid_seven() -> None:
    from app.shared.ids import new_id

    assert new_id().version == 7
    assert isinstance(new_id(), uuid.UUID)


@pytest.mark.asyncio
async def test_error_responses_are_problem_json() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/api/users/me")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["status"] == 401


@pytest.mark.asyncio
async def test_contract_headers_are_on_every_response() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http:
        response = await http.get("/api/health")
    assert len(response.headers["X-Garden-Contract-Version"]) == 12
    assert response.headers["X-Garden-Min-CLI-Version"] == "0.1.0"
