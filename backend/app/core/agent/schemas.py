"""Request and response bodies of the agent routes: CLI logins, confirmations and model usage."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.agent.service import ConfirmationView
from app.core.users import CliLogin


class ConfirmationOut(BaseModel):
    id: UUID
    operation: str
    method: str
    target: str
    # The request as the person reads it before deciding.
    summary: str | None
    status: str
    expires_at: str

    @classmethod
    def of(cls, view: ConfirmationView) -> "ConfirmationOut":
        return cls(
            id=view.id,
            operation=view.operation,
            method=view.method,
            target=view.target,
            summary=view.summary,
            status=view.status.value,
            expires_at=view.expires_at.isoformat(),
        )


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]


class UsageTodayOut(BaseModel):
    day: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens_limit: int


class ModelInfoOut(BaseModel):
    id: str
    object: Literal["model"] = "model"
    owned_by: str = "garden"


class ModelListOut(BaseModel):
    """The models the gateway forwards, as an OpenAI-style list, and the one `default` names.

    `default` is the model dsh uses. It is null when no model is allowed.
    """

    object: Literal["list"] = "list"
    data: list[ModelInfoOut]
    default: str | None


class ManifestArtifact(BaseModel):
    url: str
    sha256: str
    size_bytes: int


class ManifestOut(BaseModel):
    """The files the installer fetches, keyed by name, each with the SHA-256 it must match."""

    artifacts: dict[str, ManifestArtifact]


class CliLoginIn(BaseModel):
    """What the CLI sends after its browser returned the code: the PKCE verifier and the nonce."""

    code: str = Field(min_length=1, max_length=2048)
    code_verifier: str = Field(min_length=43, max_length=128)
    redirect_uri: str = Field(max_length=300)
    nonce: str = Field(min_length=16, max_length=128)
    device: str = Field(min_length=1, max_length=120)


class CliLoginCreated(BaseModel):
    """The new CLI login. `token` is shown once, and only its hash is kept."""

    id: UUID
    device: str
    created_at: datetime
    expires_at: datetime | None = None
    source: Literal["cli", "web"] = "cli"
    token: str


class CliLoginOut(BaseModel):
    id: UUID
    device: str
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None
    source: Literal["cli", "web"]

    @classmethod
    def of(cls, row: CliLogin) -> "CliLoginOut":
        return cls(
            id=row.id,
            device=row.device,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            revoked_at=row.revoked_at,
            expires_at=row.expires_at,
            source=row.source,
        )


class WebCliLoginIn(BaseModel):
    device: str = Field(min_length=1, max_length=120)
    expires_in_days: Literal[30, 90, 365] | None = 90
