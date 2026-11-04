"""The model gateway: forwards OpenAI-compatible chat requests and counts usage only."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Annotated, Any, cast

import httpx
from fastapi import Depends

from app.config import Settings, get_settings
from app.core.gateway.models import Usage
from app.core.gateway.repository import GatewayRepository, get_gateway_repository
from app.shared.clock import Clock, get_clock
from app.shared.errors import Forbidden, ProblemError, TooManyRequests, Unprocessable
from app.shared.ids import new_id

UPSTREAM_TIMEOUT_SECONDS = 60.0
RATE_WINDOW = timedelta(minutes=1)


class GatewayUnavailable(ProblemError):
    """503: the portal has no upstream model configured, so the gateway cannot answer."""

    status = HTTPStatus.SERVICE_UNAVAILABLE
    title = HTTPStatus.SERVICE_UNAVAILABLE.phrase


class UpstreamFailed(ProblemError):
    """502: the upstream model could not be reached or answered with an error."""

    status = HTTPStatus.BAD_GATEWAY
    title = HTTPStatus.BAD_GATEWAY.phrase


def get_upstream_transport() -> httpx.AsyncBaseTransport | None:
    """The transport to the upstream. None uses the network. Tests pass a fake upstream."""
    return None


@dataclass(frozen=True)
class UsageToday:
    day: str
    requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    tokens_limit: int


def _start_of_day(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _count(value: object) -> int:
    """Token counts from the upstream. Anything that is not a whole number counts as zero."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class GatewayService:
    def __init__(
        self,
        repo: GatewayRepository,
        clock: Clock,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._settings = settings
        self._transport = transport

    def models(self) -> list[dict[str, str]]:
        return [
            {"id": name, "object": "model", "owned_by": "garden"}
            for name in sorted(self._settings.allowed_models)
        ]

    async def complete(
        self, *, person_id: uuid.UUID, cli_login_id: uuid.UUID | None, body: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._settings.gateway_upstream_url:
            raise GatewayUnavailable("The model gateway has no upstream model configured.")
        if body.get("stream") is True:
            raise Unprocessable(
                "Streaming is not supported yet. Leave stream out or set it to false."
            )
        model = body.get("model")
        if not isinstance(model, str) or model not in self._settings.allowed_models:
            raise Forbidden("This model is not on the gateway allowlist.")

        now = self._clock.now()
        await self._check_limits(person_id=person_id, cli_login_id=cli_login_id, now=now)
        try:
            response = await self._forward(body)
        except UpstreamFailed:
            self._record(person_id, cli_login_id, model, now, "upstream_error", {})
            await self._repo.commit()
            raise
        self._record(person_id, cli_login_id, model, now, "ok", response.get("usage") or {})
        await self._repo.commit()
        return response

    async def _check_limits(
        self, *, person_id: uuid.UUID, cli_login_id: uuid.UUID | None, now: datetime
    ) -> None:
        recent = await self._repo.requests_since(
            person_id=person_id, cli_login_id=cli_login_id, since=now - RATE_WINDOW
        )
        if recent >= self._settings.gateway_requests_per_minute:
            raise TooManyRequests(
                "Too many requests in the last minute. Wait a minute and try again.",
                headers={"Retry-After": "60"},
            )
        _, _, _, used = await self._repo.totals_since(person_id=person_id, since=_start_of_day(now))
        if used >= self._settings.gateway_tokens_per_day:
            raise TooManyRequests(
                "The daily token allowance is used up. It resets at midnight UTC.",
            )

    async def _forward(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._settings.gateway_upstream_url.rstrip('/')}/chat/completions"
        headers: dict[str, str] = {}
        key = self._settings.gateway_api_key.get_secret_value()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=UPSTREAM_TIMEOUT_SECONDS
            ) as client:
                reply = await client.post(url, json=body, headers=headers)
            parsed: object = reply.json()
        except (httpx.HTTPError, ValueError) as error:
            raise UpstreamFailed("The upstream model could not answer.") from error
        if reply.status_code >= 400 or not isinstance(parsed, dict):
            raise UpstreamFailed("The upstream model could not answer.")
        return cast("dict[str, Any]", parsed)

    def _record(
        self,
        person_id: uuid.UUID,
        cli_login_id: uuid.UUID | None,
        model: str,
        now: datetime,
        status: str,
        usage: dict[str, Any],
    ) -> None:
        counts = usage
        prompt = _count(counts.get("prompt_tokens"))
        completion = _count(counts.get("completion_tokens"))
        total = _count(counts.get("total_tokens")) or prompt + completion
        self._repo.add(
            Usage(
                id=new_id(),
                person_id=person_id,
                cli_login_id=cli_login_id,
                occurred_at=now,
                model=model,
                status=status,
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=total,
            )
        )

    async def usage_today(self, *, person_id: uuid.UUID) -> UsageToday:
        now = self._clock.now()
        requests, prompt, completion, total = await self._repo.totals_since(
            person_id=person_id, since=_start_of_day(now)
        )
        return UsageToday(
            day=now.astimezone(UTC).date().isoformat(),
            requests=requests,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            tokens_limit=self._settings.gateway_tokens_per_day,
        )


def get_gateway_service(
    repo: Annotated[GatewayRepository, Depends(get_gateway_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    transport: Annotated[httpx.AsyncBaseTransport | None, Depends(get_upstream_transport)],
) -> GatewayService:
    return GatewayService(repo, clock, settings, transport)
