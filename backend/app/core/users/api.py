"""HTTP routes of the users module: browser sign-in, sign-out, the caller and the directory."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings
from app.core.users.access import SESSION_COOKIE, require
from app.core.users.schemas import (
    CliLoginCreated,
    CliLoginIn,
    CliLoginOut,
    MeOut,
    PersonOut,
)
from app.core.users.service import CurrentPerson, UserService, get_user_service
from app.shared.operations import op

router = APIRouter()


@router.get(
    "/auth/login",
    **op(
        "users.session.start",
        "Start a browser sign-in. Redirects the browser to the identity provider.",
        tool=False,
    ),
)
async def start_sign_in(
    service: Annotated[UserService, Depends(get_user_service)],
    return_to: str = "/",
) -> RedirectResponse:
    return RedirectResponse(await service.start_sign_in(return_to), status_code=302)


@router.get(
    "/auth/callback",
    **op(
        "users.session.finish",
        "Finish a browser sign-in. Sets the session cookie and redirects into the app.",
        tool=False,
    ),
)
async def finish_sign_in(
    service: Annotated[UserService, Depends(get_user_service)],
    settings: Annotated[Settings, Depends(get_settings)],
    code: str,
    state: str,
) -> RedirectResponse:
    raw_token, return_to = await service.finish_sign_in(code=code, state=state)
    response = RedirectResponse(return_to, status_code=302)
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        httponly=True,
        samesite="lax",
        # Browsers only send secure cookies over https, which every environment but dev uses.
        secure=settings.environment != "dev",
        path="/",
    )
    return response


@router.post(
    "/api/users/sign-out",
    status_code=204,
    **op("users.session.end", "End the browser session of the caller."),
)
async def sign_out(
    request: Request,
    service: Annotated[UserService, Depends(get_user_service)],
) -> Response:
    await service.sign_out(request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.get(
    "/api/users/me",
    response_model=MeOut,
    **op(
        "users.self.read",
        "Return the signed-in person, their roles and their effective permissions.",
    ),
)
async def read_me(
    person: Annotated[CurrentPerson, require("users.self")],
) -> MeOut:
    return MeOut.of(person)


@router.get(
    "/api/users/people",
    response_model=list[PersonOut],
    **op(
        "users.directory.list",
        "List the active people the caller may see. Managers see their department, "
        "finance and admin see everyone.",
    ),
)
async def list_people(
    person: Annotated[CurrentPerson, require("users.directory.view")],
    service: Annotated[UserService, Depends(get_user_service)],
) -> list[PersonOut]:
    return [PersonOut.of(view) for view in await service.visible_people(person)]


@router.get(
    "/api/cli-logins/authorize",
    **op(
        "users.cli_login.start",
        "Start a CLI login. Redirects the browser to the identity provider, which returns the code "
        "to a listener on this machine.",
        tool=False,
    ),
)
async def start_cli_login(
    service: Annotated[UserService, Depends(get_user_service)],
    redirect_uri: Annotated[str, Query(max_length=300)],
    state: Annotated[str, Query(min_length=16, max_length=128)],
    nonce: Annotated[str, Query(min_length=16, max_length=128)],
    code_challenge: Annotated[str, Query(min_length=43, max_length=128)],
) -> RedirectResponse:
    url = await service.cli_login_authorize_url(
        redirect_uri=redirect_uri, state=state, nonce=nonce, code_challenge=code_challenge
    )
    return RedirectResponse(url, status_code=302)


@router.post(
    "/api/cli-logins",
    response_model=CliLoginCreated,
    status_code=201,
    **op(
        "users.cli_login.create",
        "Finish a CLI login with the code the browser returned. The token is shown once.",
        tool=False,
    ),
)
async def create_cli_login(
    body: CliLoginIn,
    service: Annotated[UserService, Depends(get_user_service)],
) -> CliLoginCreated:
    token, row = await service.create_cli_login(
        code=body.code,
        code_verifier=body.code_verifier,
        redirect_uri=body.redirect_uri,
        nonce=body.nonce,
        device=body.device,
    )
    return CliLoginCreated(id=row.id, device=row.device, created_at=row.created_at, token=token)


@router.get(
    "/api/cli-logins",
    response_model=list[CliLoginOut],
    **op("users.cli_login.list", "List the caller's CLI logins, newest first, with their state."),
)
async def list_cli_logins(
    person: Annotated[CurrentPerson, require("cli_logins.view")],
    service: Annotated[UserService, Depends(get_user_service)],
) -> list[CliLoginOut]:
    return [CliLoginOut.of(row) for row in await service.list_cli_logins(person.id)]


@router.delete(
    "/api/cli-logins/{login_id}",
    status_code=204,
    **op(
        "users.cli_login.revoke",
        "Revoke one of the caller's CLI logins. Only a browser sign-in may do this.",
        tool=False,
    ),
)
async def revoke_cli_login(
    login_id: UUID,
    person: Annotated[CurrentPerson, require("cli_logins.revoke")],
    service: Annotated[UserService, Depends(get_user_service)],
) -> Response:
    await service.revoke_cli_login(person_id=person.id, login_id=login_id)
    return Response(status_code=204)
