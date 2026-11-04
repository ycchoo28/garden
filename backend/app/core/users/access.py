"""FastAPI dependencies that identify the caller and check permissions. Routes depend on these."""

from collections.abc import Callable
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Request, params
from fastapi.routing import APIRoute

from app.core.users.service import CurrentPerson, UserService, get_user_service
from app.shared.errors import Forbidden
from app.shared.request_login import VIA_AGENT, VIA_WEB, RequestLogin, current_request_login

SESSION_COOKIE = "garden_session"

# Each check dependency maps to the permission it enforces, so the contract can advertise it.
_PERMISSION_OF: dict[Callable[..., object], str] = {}


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def current_person(
    request: Request,
    service: Annotated[UserService, Depends(get_user_service)],
) -> CurrentPerson:
    """The person behind the request. A CLI login's bearer token wins over the browser cookie.

    Every request sets its own login here, so one request's sign-in never leaks into the next.
    """
    bearer = _bearer_token(request)
    if bearer is not None:
        agent_allowed = frozenset(
            permission.name
            for permission in request.app.state.permissions
            if permission.agent_allowed
        )
        person, login = await service.current_cli_person(
            bearer, request.app.state.roles, agent_allowed
        )
        _ = current_request_login.set(
            RequestLogin(
                via=VIA_AGENT,
                device=login.device,
                created_at=login.created_at,
                cli_login_id=login.id,
            )
        )
        return person
    person = await service.current_person(
        request.cookies.get(SESSION_COOKIE), request.app.state.roles
    )
    _ = current_request_login.set(RequestLogin(via=VIA_WEB))
    return person


def require(permission: str) -> params.Depends:
    """Route dependency that refuses callers without `permission`."""

    async def check(
        request: Request,
        person: Annotated[CurrentPerson, Depends(current_person)],
    ) -> CurrentPerson:
        if permission not in person.permissions:
            raise Forbidden(f"You need the {permission} permission for this.")
        # A CLI login never holds a permission its declaration marks as person-only.
        login = current_request_login.get()
        if login is not None and login.via == VIA_AGENT and not _agent_allowed(request, permission):
            raise Forbidden(f"Only a person can use {permission}. Ask the person to do this.")
        return person

    _PERMISSION_OF[check] = permission
    return Depends(check)


def _agent_allowed(request: Request, permission: str) -> bool:
    declared = [item for item in request.app.state.permissions if item.name == permission]
    return bool(declared) and declared[0].agent_allowed


def route_permission(route: APIRoute) -> str | None:
    """The permission a route's check enforces, or None when the route has no check."""
    for dependency in route.dependant.dependencies:
        call = dependency.call
        if call is not None and call in _PERMISSION_OF:
            return _PERMISSION_OF[call]
    return None


def advertise_permissions(app: FastAPI) -> None:
    """Write `x-permission` onto every operation that a permission check guards."""
    build_openapi = app.openapi

    def openapi() -> dict[str, object]:
        schema: dict[str, object] = build_openapi()
        paths_value = schema.get("paths")
        if not isinstance(paths_value, dict):
            return schema
        paths = cast("dict[str, object]", paths_value)
        for route in app.routes:
            if not isinstance(route, APIRoute) or not route.include_in_schema:
                continue
            permission = route_permission(route)
            if permission is None:
                continue
            path_item = paths.get(route.path_format)
            if not isinstance(path_item, dict):
                continue
            operations = cast("dict[str, object]", path_item)
            for method in route.methods or ():
                operation = operations.get(method.lower())
                if isinstance(operation, dict):
                    cast("dict[str, object]", operation)["x-permission"] = permission
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
