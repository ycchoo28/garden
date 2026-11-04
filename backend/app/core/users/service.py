"""Sign-in, browser sessions and what a signed-in person may see. The business rules live here."""

import base64
import hashlib
import re
import secrets
import uuid
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.core.users.identity import IdentityProvider, IdentityRejected
from app.core.users.models import CliLogin, Person, SignInAttempt, WebSession
from app.core.users.oidc import get_identity_provider
from app.core.users.permissions import EMPLOYEE
from app.core.users.repository import UserRepository, get_user_repository
from app.core.users.scope import DataScope
from app.shared.clock import Clock, get_clock
from app.shared.errors import Forbidden, NotFound, Unauthorized
from app.shared.ids import new_id
from app.shared.permissions import Role

# A sign-in must finish within this time, or its link is refused and the person starts again.
SIGN_IN_TIMEOUT = timedelta(minutes=10)
# A CLI login sends the browser back to a listener on this machine only, never to another host.
_LOOPBACK_CALLBACK = re.compile(r"http://127\.0\.0\.1:\d{1,5}/callback")


def token_hash(token: str) -> str:
    """Only this hash is stored, so a copy of the database cannot be replayed as a session."""
    return hashlib.sha256(token.encode()).hexdigest()


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _safe_return_to(value: str) -> str:
    # Only same-site paths. An absolute URL would send people off the site after sign-in.
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/"
    return value


@dataclass(frozen=True)
class CurrentPerson:
    id: uuid.UUID
    name: str
    email: str
    department_id: uuid.UUID
    department_code: str
    department_name: str
    roles: frozenset[str]
    permissions: frozenset[str]
    scope: DataScope


@dataclass(frozen=True)
class PersonView:
    id: uuid.UUID
    name: str
    email: str
    department_code: str
    department_name: str


class UserService:
    def __init__(
        self,
        repo: UserRepository,
        identity: IdentityProvider,
        settings: Settings,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._identity = identity
        self._settings = settings
        self._clock = clock

    def _redirect_uri(self) -> str:
        return f"{self._settings.public_url.rstrip('/')}/auth/callback"

    async def start_sign_in(self, return_to: str) -> str:
        now = self._clock.now()
        await self._repo.purge_sign_in_attempts(now - SIGN_IN_TIMEOUT)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        await self._repo.add_sign_in_attempt(
            SignInAttempt(
                state=state,
                code_verifier=verifier,
                nonce=nonce,
                return_to=_safe_return_to(return_to),
                created_at=now,
            )
        )
        await self._repo.commit()
        return await self._identity.authorization_url(
            redirect_uri=self._redirect_uri(),
            state=state,
            nonce=nonce,
            code_challenge=_pkce_challenge(verifier),
        )

    async def finish_sign_in(self, *, code: str, state: str) -> tuple[str, str]:
        """Return the raw session token and the path to send the person to."""
        now = self._clock.now()
        attempt = await self._repo.take_sign_in_attempt(state)
        # The attempt is spent even when the rest fails, so a state value works once.
        await self._repo.commit()
        if attempt is None or now - attempt.created_at > SIGN_IN_TIMEOUT:
            raise Unauthorized("This sign-in link has expired. Start again.")
        try:
            tokens = await self._identity.exchange_code(
                code=code, code_verifier=attempt.code_verifier, redirect_uri=self._redirect_uri()
            )
            claims = await self._identity.verify_id_token(tokens.id_token, nonce=attempt.nonce)
        except IdentityRejected as error:
            raise Unauthorized("The identity provider did not confirm this sign-in.") from error
        person = await self._repo.person_by_subject(claims.subject)
        if person is None or not person.is_active:
            raise Forbidden("This account is not set up for Garden.")
        raw_token = secrets.token_urlsafe(32)
        self._repo.add_web_session(
            WebSession(
                token_hash=token_hash(raw_token),
                person_id=person.id,
                created_at=now,
                last_seen_at=now,
            )
        )
        await self._repo.commit()
        return raw_token, attempt.return_to

    async def current_person(self, raw_token: str | None, roles: tuple[Role, ...]) -> CurrentPerson:
        if not raw_token:
            raise Unauthorized("Sign in to continue.")
        now = self._clock.now()
        hashed = token_hash(raw_token)
        found = await self._repo.web_session_with_person(hashed)
        if found is None:
            raise Unauthorized("Sign in to continue.")
        session, person = found
        if now - session.last_seen_at > timedelta(minutes=self._settings.session_idle_minutes):
            await self._repo.delete_web_session(hashed)
            await self._repo.commit()
            raise Unauthorized("Your session ended after inactivity. Sign in again.")
        if not person.is_active:
            raise Forbidden("This account is not set up for Garden.")
        await self._repo.touch_web_session(hashed, now)
        await self._repo.commit()
        return await self._assemble(person, roles)

    async def sign_out(self, raw_token: str | None) -> None:
        if raw_token:
            await self._repo.delete_web_session(token_hash(raw_token))
            await self._repo.commit()

    async def _assemble(self, person: Person, roles: tuple[Role, ...]) -> CurrentPerson:
        department = await self._repo.department_by_id(person.department_id)
        if department is None:
            raise Forbidden("This account has no department.")
        held = frozenset({EMPLOYEE}) | await self._repo.role_names(person.id)
        grants: dict[str, frozenset[str]] = {role.name: role.permissions for role in roles}
        permissions: set[str] = set()
        for name in held:
            permissions |= grants.get(name, frozenset())
        return CurrentPerson(
            id=person.id,
            name=person.name,
            email=person.email,
            department_id=department.id,
            department_code=department.code,
            department_name=department.name,
            roles=held,
            permissions=frozenset(permissions),
            scope=DataScope.for_roles(held, department.id),
        )

    async def cli_login_authorize_url(
        self, *, redirect_uri: str, state: str, nonce: str, code_challenge: str
    ) -> str:
        """Where the CLI's browser goes to approve its login. The CLI keeps the verifier itself."""
        if _LOOPBACK_CALLBACK.fullmatch(redirect_uri) is None:
            raise Forbidden("A CLI login returns only to a listener on this machine.")
        return await self._identity.authorization_url(
            redirect_uri=redirect_uri,
            state=state,
            nonce=nonce,
            code_challenge=code_challenge,
        )

    async def create_cli_login(
        self, *, code: str, code_verifier: str, redirect_uri: str, nonce: str, device: str
    ) -> tuple[str, CliLogin]:
        """Trade the approved code for a CLI login. The raw token is returned once."""
        if _LOOPBACK_CALLBACK.fullmatch(redirect_uri) is None:
            raise Forbidden("A CLI login returns only to a listener on this machine.")
        now = self._clock.now()
        try:
            tokens = await self._identity.exchange_code(
                code=code, code_verifier=code_verifier, redirect_uri=redirect_uri
            )
            claims = await self._identity.verify_id_token(tokens.id_token, nonce=nonce)
        except IdentityRejected as error:
            raise Unauthorized("The identity provider did not confirm this sign-in.") from error
        person = await self._repo.person_by_subject(claims.subject)
        if person is None or not person.is_active:
            raise Forbidden("This account is not set up for Garden.")
        raw_token = secrets.token_urlsafe(32)
        row = CliLogin(
            id=new_id(),
            person_id=person.id,
            token_hash=token_hash(raw_token),
            device=device,
            created_at=now,
            last_used_at=None,
            revoked_at=None,
        )
        self._repo.add_cli_login(row)
        await self._repo.commit()
        return raw_token, row

    async def current_cli_person(
        self,
        raw_token: str,
        roles: tuple[Role, ...],
        agent_allowed: frozenset[str],
    ) -> tuple[CurrentPerson, CliLogin]:
        """The person behind a CLI login. The login holds only permissions an agent may hold."""
        now = self._clock.now()
        found = await self._repo.cli_login_with_person(token_hash(raw_token))
        if found is None:
            raise Unauthorized("Run garden login again. This CLI login is not recognised.")
        login, person = found
        if login.revoked_at is not None:
            raise Unauthorized("This CLI login was revoked. Run garden login again.")
        if not person.is_active:
            raise Forbidden("This account is not set up for Garden.")
        await self._repo.touch_cli_login(token_hash(raw_token), now)
        await self._repo.commit()
        assembled = await self._assemble(person, roles)
        held = assembled.permissions & agent_allowed
        return replace(assembled, permissions=frozenset(held)), login

    async def list_cli_logins(self, person_id: uuid.UUID) -> list[CliLogin]:
        return list(await self._repo.cli_logins_of(person_id))

    async def revoke_cli_login(self, *, person_id: uuid.UUID, login_id: uuid.UUID) -> None:
        """Only the person may revoke. A login that is already revoked stays revoked."""
        login = await self._repo.cli_login_of(person_id, login_id)
        if login is None:
            raise NotFound("No CLI login has that identifier.")
        if login.revoked_at is None:
            login.revoked_at = self._clock.now()
            await self._repo.commit()

    async def visible_people(self, caller: CurrentPerson) -> list[PersonView]:
        departments = None if caller.scope.all_departments else caller.scope.department_ids
        rows = await self._repo.people_in(departments, include=caller.id)
        return [
            PersonView(
                id=person.id,
                name=person.name,
                email=person.email,
                department_code=department.code,
                department_name=department.name,
            )
            for person, department in rows
        ]


def get_user_service(
    repo: Annotated[UserRepository, Depends(get_user_repository)],
    identity: Annotated[IdentityProvider, Depends(get_identity_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
    clock: Annotated[Clock, Depends(get_clock)],
) -> UserService:
    return UserService(repo, identity, settings, clock)
