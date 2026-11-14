"""HTTP routes of the agent module: a person approves CLI logins and manages them, and an agent
reads its confirmation while the person decides it in the browser.

Routes only check permissions and call services. The model gateway's routes are in model_api.py.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
from garden.mcp import HostedMCPSettings, create_streamable_http_app
from starlette.routing import Match, Mount
from starlette.types import Scope

from app.config import Settings, get_settings
from app.core.agent.downloads import download_path, offered_files
from app.core.agent.model_api import router as model_router
from app.core.agent.schemas import (
    CliLoginCreated,
    CliLoginIn,
    CliLoginOut,
    ConfirmationOut,
    DecisionIn,
    ManifestArtifact,
    ManifestOut,
    WebCliLoginIn,
)
from app.core.agent.service import ConfirmationService, get_confirmation_service
from app.core.audit import AuditService, get_audit_service
from app.core.users import CurrentPerson, UserService, get_user_service, require
from app.shared.errors import Forbidden
from app.shared.operations import op
from app.shared.request_login import current_request_login

router = APIRouter()
# The model gateway's routes are declared in their own file, and this router serves them too.
router.include_router(model_router)


def _hosted_mcp_settings() -> HostedMCPSettings:
    settings = get_settings()
    return HostedMCPSettings(
        enabled=settings.agent_mcp_enabled,
        api_base_url=settings.agent_mcp_api_base_url or None,
        allowed_origins=settings.agent_mcp_origins,
        files_max_bytes=settings.files_max_bytes,
        pending_per_login=settings.agent_mcp_pending_per_login,
        pending_total=settings.agent_mcp_pending_total,
    )


# The self-contained ASGI app handles MCP. This module only mounts it and provides settings.
class _ExactMCPMount(Mount):
    """Mount the ASGI app at the exact operation URL without Starlette's slash redirect."""

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        path = scope.get("path", "")
        root_path = scope.get("root_path", "")
        route_path = path[len(root_path) :] if root_path and path.startswith(root_path) else path
        if scope["type"] == "http" and route_path == self.path:
            matching_scope = {**scope, "path": f"{path}/"}
            return super().matches(matching_scope)
        return super().matches(scope)


router.routes.append(
    _ExactMCPMount("/api/agent/mcp", create_streamable_http_app(_hosted_mcp_settings))
)


# The installer's manifest and files need no sign-in: they hold no secrets, and the installer
# fetches them before the person has a login. These two routes are the public exception.


@router.get(
    "/api/agent/manifest",
    response_model=ManifestOut,
    **op(
        "agent.manifest.read",
        "List the files the installer fetches, with the SHA-256 each must match. Needs no sign-in.",
        tool=False,
        confirm=False,
    ),
)
async def read_manifest(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ManifestOut:
    return ManifestOut(
        artifacts={
            file.name: ManifestArtifact(
                url=f"/api/agent/downloads/{file.name}",
                sha256=file.sha256,
                size_bytes=file.size_bytes,
            )
            for file in offered_files(settings.agent_downloads_dir)
        }
    )


@router.get(
    "/api/agent/downloads/{name}",
    response_class=FileResponse,
    **op(
        "agent.download.read",
        "Download one garden or dsh file, or an installer, by name. Needs no sign-in.",
        tool=False,
        confirm=False,
    ),
)
async def read_download(
    name: str,
    settings: Annotated[Settings, Depends(get_settings)],
) -> FileResponse:
    return FileResponse(download_path(settings.agent_downloads_dir, name), filename=name)


@router.get(
    "/api/agent/cli/authorize",
    **op(
        "agent.cli_approval.start",
        "Start a CLI login. Redirects the browser to the identity provider, which returns the code "
        "to a listener on this machine.",
        tool=False,
        confirm=False,
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
    "/api/agent/cli/approvals",
    response_model=CliLoginCreated,
    status_code=201,
    **op(
        "agent.cli_approval.create",
        "Finish a CLI login with the code the browser returned. The token is shown once.",
        tool=False,
        confirm=False,
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
    return CliLoginCreated(
        id=row.id,
        device=row.device,
        created_at=row.created_at,
        expires_at=row.expires_at,
        source=row.source,
        token=token,
    )


@router.post(
    "/api/agent/my-logins",
    response_model=CliLoginCreated,
    status_code=201,
    **op(
        "agent.my_login.create",
        "Create a CLI login for a hosted agent. Its token is shown once.",
        tool=False,
        confirm=False,
    ),
)
async def create_browser_cli_login(
    body: WebCliLoginIn,
    person: Annotated[CurrentPerson, require("agent.logins.create")],
    service: Annotated[UserService, Depends(get_user_service)],
    audit: Annotated[AuditService, Depends(get_audit_service)],
) -> CliLoginCreated:
    token, row = await service.create_web_cli_login(
        person_id=person.id,
        device=body.device,
        expires_in_days=body.expires_in_days,
    )
    audit.record(
        actor_id=person.id,
        operation="agent.my_login.create",
        target=str(row.id),
        summary=f"Created Agent login {row.device}",
    )
    await service.commit()
    return CliLoginCreated(
        id=row.id,
        device=row.device,
        created_at=row.created_at,
        expires_at=row.expires_at,
        source=row.source,
        token=token,
    )


@router.get(
    "/api/agent/my-logins",
    response_model=list[CliLoginOut],
    **op(
        "agent.my_login.list",
        "List the caller's CLI logins, newest first, with their state.",
        confirm=False,
    ),
)
async def list_cli_logins(
    person: Annotated[CurrentPerson, require("agent.logins.view")],
    service: Annotated[UserService, Depends(get_user_service)],
) -> list[CliLoginOut]:
    return [CliLoginOut.of(row) for row in await service.list_cli_logins(person.id)]


@router.post(
    "/api/agent/my-logins/{login_id}/sign-out",
    status_code=204,
    **op(
        "agent.my_login.sign_out",
        "Revoke one of the caller's CLI logins. Only a browser sign-in may do this.",
        tool=False,
        confirm=False,
    ),
)
async def revoke_cli_login(
    login_id: UUID,
    person: Annotated[CurrentPerson, require("agent.logins.revoke")],
    service: Annotated[UserService, Depends(get_user_service)],
) -> Response:
    await service.revoke_cli_login(person_id=person.id, login_id=login_id)
    return Response(status_code=204)


# A CLI login ends itself: `garden logout` calls this with the login's own token, so the portal
# stops it and not only the laptop forgetting it. A browser session has no CLI login to end.
@router.delete(
    "/api/agent/cli/token",
    status_code=204,
    **op(
        "agent.cli_token.sign_out",
        "End this device's CLI login. The token used for this call stops working.",
        tool=False,
        confirm=False,
    ),
)
async def sign_out_cli_token(
    person: Annotated[CurrentPerson, require("users.self")],
    service: Annotated[UserService, Depends(get_user_service)],
) -> Response:
    login = current_request_login.get()
    if login is None or login.cli_login_id is None:
        raise Forbidden("Only an Agent login ends with this call. Sign out in the browser instead.")
    await service.revoke_cli_login(person_id=person.id, login_id=login.cli_login_id)
    return Response(status_code=204)


@router.get(
    "/api/agent/my-confirmations/{confirmation_id}",
    response_model=ConfirmationOut,
    **op(
        "agent.confirmation.read",
        "Check whether the person has approved a confirmation.",
        confirm=False,
    ),
)
async def read_confirmation(
    confirmation_id: UUID,
    person: Annotated[CurrentPerson, require("users.self")],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ConfirmationOut:
    return ConfirmationOut.of(
        await service.view(confirmation_id=confirmation_id, person_id=person.id)
    )


@router.post(
    "/api/agent/my-confirmations/{confirmation_id}/decision",
    response_model=ConfirmationOut,
    **op(
        "agent.confirmation.decide",
        "Approve or reject a pending confirmation. Only a browser sign-in may do this.",
        tool=False,
        confirm=False,
    ),
)
async def decide_confirmation(
    confirmation_id: UUID,
    body: DecisionIn,
    person: Annotated[CurrentPerson, require("agent.confirmations.decide")],
    service: Annotated[ConfirmationService, Depends(get_confirmation_service)],
) -> ConfirmationOut:
    view = await service.decide(
        confirmation_id=confirmation_id,
        person_id=person.id,
        approve=body.decision == "approve",
    )
    return ConfirmationOut.of(view)
