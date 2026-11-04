"""The confirmation gate. A critical agent action runs only after the person approves it."""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from http import HTTPStatus
from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.core.confirmations.models import Confirmation
from app.core.confirmations.repository import ConfirmationRepository, get_confirmation_repository
from app.shared.clock import Clock, get_clock
from app.shared.errors import Conflict, Forbidden, NotFound, ProblemError, Unprocessable
from app.shared.ids import new_id
from app.shared.request_login import RequestLogin

LIFETIME = timedelta(minutes=10)
# The request body the person reads on the confirmation page is cut at this many characters.
SUMMARY_LIMIT = 2000


class ConfirmationRequired(ProblemError):
    """428: the person must approve first. The extensions say where to send them."""

    status = HTTPStatus.PRECONDITION_REQUIRED
    title = "Confirmation Required"


class ConfirmationStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    USED = "used"


@dataclass(frozen=True)
class ConfirmationView:
    id: uuid.UUID
    operation: str
    method: str
    target: str
    summary: str | None
    status: ConfirmationStatus
    expires_at: datetime


def login_fingerprint(login: RequestLogin) -> str:
    """Ties a confirmation to the CLI login that asked for it, without storing the login itself."""
    created = login.created_at.isoformat() if login.created_at else ""
    return hashlib.sha256(f"{login.device or ''}|{created}".encode()).hexdigest()


def args_digest(*, operation: str, method: str, target: str, body: bytes) -> str:
    """A SHA-256 over the operation, method, target and canonical JSON body of the request."""
    try:
        parsed: object = json.loads(body) if body else None
    except json.JSONDecodeError as error:
        raise Unprocessable("The request body must be JSON.") from error
    canonical = json.dumps(
        [operation, method, target, parsed], sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def request_summary(*, method: str, target: str, body: bytes) -> str:
    """The request as the person reads it: the method, the target and the JSON body, cut short."""
    try:
        pretty = json.dumps(json.loads(body), indent=2, ensure_ascii=False) if body else ""
    except json.JSONDecodeError:
        pretty = ""
    text = f"{method} {target}" + (f"\n{pretty}" if pretty else "")
    return text[:SUMMARY_LIMIT]


def status_of(row: Confirmation, now: datetime) -> ConfirmationStatus:
    if row.used_at is not None:
        return ConfirmationStatus.USED
    if row.approved is None:
        return ConfirmationStatus.EXPIRED if now > row.expires_at else ConfirmationStatus.PENDING
    if not row.approved:
        return ConfirmationStatus.REJECTED
    return ConfirmationStatus.EXPIRED if now > row.expires_at else ConfirmationStatus.APPROVED


class ConfirmationService:
    def __init__(self, repo: ConfirmationRepository, clock: Clock, settings: Settings) -> None:
        self._repo = repo
        self._clock = clock
        self._settings = settings

    def _required(self, row: Confirmation) -> ConfirmationRequired:
        return ConfirmationRequired(
            detail="The person must approve this action in the browser first.",
            extensions={
                "confirmation_id": str(row.id),
                "confirmation_url": (f"{self._settings.public_url.rstrip('/')}/confirm/{row.id}"),
                "expires_at": row.expires_at.isoformat(),
            },
        )

    async def require_approval(
        self,
        *,
        person_id: uuid.UUID,
        login: RequestLogin,
        operation: str,
        method: str,
        target: str,
        body: bytes,
        confirmation_id: str | None,
    ) -> None:
        """Pass only with an approved, unused, matching confirmation. Otherwise answer 428."""
        digest = args_digest(operation=operation, method=method, target=target, body=body)
        fingerprint = login_fingerprint(login)
        now = self._clock.now()
        if confirmation_id is None:
            row = Confirmation(
                id=new_id(),
                person_id=person_id,
                login_fingerprint=fingerprint,
                operation=operation,
                method=method,
                target=target,
                args_digest=digest,
                created_at=now,
                expires_at=now + LIFETIME,
                decided_at=None,
                approved=None,
                used_at=None,
                request_summary=request_summary(method=method, target=target, body=body),
            )
            self._repo.add(row)
            await self._repo.commit()
            raise self._required(row)

        row = await self._owned(confirmation_id, person_id)
        if (
            row.login_fingerprint != fingerprint
            or row.operation != operation
            or row.method != method
            or row.target != target
            or row.args_digest != digest
        ):
            raise Forbidden("This confirmation was made for a different request.")
        status = status_of(row, now)
        if status is ConfirmationStatus.PENDING:
            raise self._required(row)
        if status is ConfirmationStatus.USED:
            raise Conflict("This confirmation was already used.")
        if status is not ConfirmationStatus.APPROVED:
            raise Forbidden(f"This confirmation is {status.value}.")
        row.used_at = now
        await self._repo.commit()

    async def _owned(self, confirmation_id: str, person_id: uuid.UUID) -> Confirmation:
        """The confirmation, only when the caller owns it. Otherwise it looks absent."""
        try:
            parsed = uuid.UUID(confirmation_id)
        except ValueError as error:
            raise NotFound("No confirmation has that identifier.") from error
        row = await self._repo.get(parsed)
        if row is None or row.person_id != person_id:
            raise NotFound("No confirmation has that identifier.")
        return row

    async def decide(
        self, *, confirmation_id: uuid.UUID, person_id: uuid.UUID, approve: bool
    ) -> ConfirmationView:
        row = await self._owned(str(confirmation_id), person_id)
        now = self._clock.now()
        if status_of(row, now) is not ConfirmationStatus.PENDING:
            raise Conflict("This confirmation is no longer waiting for a decision.")
        row.decided_at = now
        row.approved = approve
        await self._repo.commit()
        return self._view(row, now)

    async def view(self, *, confirmation_id: uuid.UUID, person_id: uuid.UUID) -> ConfirmationView:
        row = await self._owned(str(confirmation_id), person_id)
        return self._view(row, self._clock.now())

    def _view(self, row: Confirmation, now: datetime) -> ConfirmationView:
        return ConfirmationView(
            id=row.id,
            operation=row.operation,
            method=row.method,
            target=row.target,
            summary=row.request_summary,
            status=status_of(row, now),
            expires_at=row.expires_at,
        )


def get_confirmation_service(
    repo: Annotated[ConfirmationRepository, Depends(get_confirmation_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConfirmationService:
    return ConfirmationService(repo, clock, settings)
