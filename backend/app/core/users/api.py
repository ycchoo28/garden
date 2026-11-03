"""HTTP routes of the users module: browser sign-in, sign-out, the caller and the directory."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings
from app.core.users.access import SESSION_COOKIE, require
from app.core.users.schemas import MeOut, PersonOut
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
