"""Errors that the CLI and the MCP server report, in one shape for people and for agents."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GardenError(Exception):
    """A failure the person or agent can act on. `fix` says what to do next."""

    title: str
    detail: str | None = None
    status: int | None = None
    fix: str | None = None
    errors: list[dict[str, str]] = field(default_factory=lambda: list[dict[str, str]]())

    def __post_init__(self) -> None:
        super().__init__(self.detail or self.title)

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"title": self.title}
        if self.status is not None:
            body["status"] = self.status
        if self.detail is not None:
            body["detail"] = self.detail
        if self.errors:
            body["errors"] = self.errors
        if self.fix is not None:
            body["fix"] = self.fix
        return {"error": body}


@dataclass
class ConfirmationRequired(GardenError):
    """A critical action waits for the person's approval in the browser (ADR 0012).

    The portal answers 428 with the confirmation's id and its link. The engine waits on the id, so
    the person can approve or reject the action there, and then the action runs or the tool stops.
    """

    confirmation_id: str = ""
    approve_url: str = ""
    expires_at: str = ""


class NotSignedIn(GardenError):
    def __init__(self) -> None:
        super().__init__(
            title="Not signed in",
            fix="Run `garden login` and approve the login in the browser.",
        )


class CliTooOld(GardenError):
    def __init__(self, current: str, minimum: str) -> None:
        super().__init__(
            title="This garden is too old for the portal",
            detail=f"garden {current} is older than the minimum {minimum}.",
            fix="Install the newest garden from the agent folder with `uv tool install --force`.",
        )
