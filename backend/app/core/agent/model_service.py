"""The model gateway: forwards OpenAI-compatible chat requests and counts usage only."""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Annotated, Any, cast

import anyio
import httpx
from fastapi import Depends

from app.config import Settings, get_settings
from app.core.agent.models import Usage
from app.core.agent.repository import AgentRepository, get_agent_repository
from app.core.agent.schemas import ModelInfoOut, ModelListOut
from app.shared.clock import Clock, get_clock
from app.shared.errors import Forbidden, ProblemError, TooManyRequests
from app.shared.ids import new_id

UPSTREAM_TIMEOUT_SECONDS = 60.0
# How long to keep reading a stream the client dropped, to learn what the upstream billed.
DRAIN_SECONDS = 30.0
RATE_WINDOW = timedelta(minutes=1)


class ModelUnavailable(ProblemError):
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


def _event_usage(line: str) -> dict[str, Any]:
    """The usage object carried by one `data:` event of a stream, or nothing."""
    try:
        parsed: object = json.loads(line[len("data:") :])
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    usage: object = cast("dict[str, Any]", parsed).get("usage")
    return cast("dict[str, Any]", usage) if isinstance(usage, dict) else {}


def _merge(into: dict[str, Any], found: dict[str, Any]) -> None:
    """Fold one event's usage into the stream's. Counts only grow, so the largest one wins."""
    for key, value in found.items():
        if key in into and isinstance(value, int) and isinstance(into[key], int):
            into[key] = max(into[key], value)
        else:
            into[key] = value


def _count(value: object) -> int:
    """Token counts from the upstream. Anything that is not a whole number counts as zero."""
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


class ModelService:
    def __init__(
        self,
        repo: AgentRepository,
        clock: Clock,
        settings: Settings,
        transport: httpx.AsyncBaseTransport | None,
    ) -> None:
        self._repo = repo
        self._clock = clock
        self._settings = settings
        self._transport = transport

    def models(self) -> ModelListOut:
        return ModelListOut(
            data=[ModelInfoOut(id=name) for name in sorted(self._settings.allowed_models)],
            default=self._settings.default_model,
        )

    async def complete(
        self, *, person_id: uuid.UUID, cli_login_id: uuid.UUID | None, body: dict[str, Any]
    ) -> dict[str, Any]:
        if not self._settings.agent_model_upstream_url:
            raise ModelUnavailable("The model gateway has no upstream model configured.")
        model, now = await self._admit(person_id=person_id, cli_login_id=cli_login_id, body=body)
        try:
            response = await self._forward(body)
        except UpstreamFailed:
            await self._settle(person_id, cli_login_id, model, now, "upstream_error", {})
            raise
        except BaseException:
            # The caller went away while the upstream was thinking. The call still counts.
            await self._settle(person_id, cli_login_id, model, now, "cancelled", {})
            raise
        reported: object = response.get("usage")
        usage = cast("dict[str, Any]", reported) if isinstance(reported, dict) else {}
        await self._settle(person_id, cli_login_id, model, now, "ok", usage)
        return response

    async def stream(
        self, *, person_id: uuid.UUID, cli_login_id: uuid.UUID | None, body: dict[str, Any]
    ) -> AsyncIterator[bytes]:
        """Open a streamed completion. Refusals and upstream failures raise before any bytes
        go out. The usage is asked for in the stream, so it can be recorded when the answer ends."""
        model, now = await self._admit(person_id=person_id, cli_login_id=cli_login_id, body=body)
        url = f"{self._settings.agent_model_upstream_url.rstrip('/')}/chat/completions"
        client = httpx.AsyncClient(transport=self._transport, timeout=UPSTREAM_TIMEOUT_SECONDS)
        options = body.get("stream_options")
        asked: dict[str, Any] = {"include_usage": True}
        if isinstance(options, dict):
            asked = {**cast("dict[str, Any]", options), "include_usage": True}
        upstream_body = {**body, "stream_options": asked}
        try:
            upstream = await client.send(
                client.build_request("POST", url, json=upstream_body, headers=self._headers()),
                stream=True,
            )
        except httpx.HTTPError as error:
            await client.aclose()
            await self._settle(person_id, cli_login_id, model, now, "upstream_error", {})
            raise UpstreamFailed("The upstream model could not answer.") from error
        if upstream.status_code >= 400:
            await upstream.aclose()
            await client.aclose()
            await self._settle(person_id, cli_login_id, model, now, "upstream_error", {})
            raise UpstreamFailed("The upstream model could not answer.")
        return self._relay(client, upstream, person_id, cli_login_id, model, now)

    async def _relay(
        self,
        client: httpx.AsyncClient,
        upstream: httpx.Response,
        person_id: uuid.UUID,
        cli_login_id: uuid.UUID | None,
        model: str,
        now: datetime,
    ) -> AsyncIterator[bytes]:
        """Pass the upstream's server-sent events through unchanged and count usage when it ends."""
        usage: dict[str, Any] = {}
        status = "ok"
        lines = upstream.aiter_lines()
        try:
            async for line in lines:
                if line.startswith("data:") and '"usage"' in line:
                    _merge(usage, _event_usage(line))
                yield f"{line}\n".encode()
        except httpx.HTTPError:
            status = "upstream_error"
        except BaseException:
            # The client dropped the connection. The upstream bills the whole answer anyway,
            # so read what is left to count the tokens it reports, within a bound.
            status = "cancelled"
            with anyio.CancelScope(shield=True), anyio.move_on_after(DRAIN_SECONDS):
                try:
                    async for line in lines:
                        if line.startswith("data:") and '"usage"' in line:
                            _merge(usage, _event_usage(line))
                except httpx.HTTPError:
                    pass
            raise
        finally:
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
                await client.aclose()
            await self._settle(person_id, cli_login_id, model, now, status, usage)

    async def _admit(
        self, *, person_id: uuid.UUID, cli_login_id: uuid.UUID | None, body: dict[str, Any]
    ) -> tuple[str, datetime]:
        """Check the upstream, the allowlist and the limits. Returns the model and the moment."""
        if not self._settings.agent_model_upstream_url:
            raise ModelUnavailable("The model gateway has no upstream model configured.")
        model = body.get("model")
        if not isinstance(model, str) or model not in self._settings.allowed_models:
            raise Forbidden("This model is not on the gateway allowlist.")
        now = self._clock.now()
        await self._check_limits(person_id=person_id, cli_login_id=cli_login_id, now=now)
        return model, now

    def _headers(self) -> dict[str, str]:
        key = self._settings.agent_model_api_key.get_secret_value()
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def _settle(
        self,
        person_id: uuid.UUID,
        cli_login_id: uuid.UUID | None,
        model: str,
        now: datetime,
        status: str,
        usage: dict[str, Any],
    ) -> None:
        """Write the call's usage row and commit it, even when the caller is being cancelled."""
        with anyio.CancelScope(shield=True):
            self._record(person_id, cli_login_id, model, now, status, usage)
            await self._repo.commit()

    async def _check_limits(
        self, *, person_id: uuid.UUID, cli_login_id: uuid.UUID | None, now: datetime
    ) -> None:
        recent = await self._repo.requests_since(
            person_id=person_id, cli_login_id=cli_login_id, since=now - RATE_WINDOW
        )
        if recent >= self._settings.agent_model_requests_per_minute:
            raise TooManyRequests(
                "Too many requests in the last minute. Wait a minute and try again.",
                headers={"Retry-After": "60"},
            )
        _, _, _, used = await self._repo.totals_since(person_id=person_id, since=_start_of_day(now))
        if used >= self._settings.agent_model_tokens_per_day:
            raise TooManyRequests(
                "The daily token allowance is used up. It resets at midnight UTC.",
            )

    async def _forward(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._settings.agent_model_upstream_url.rstrip('/')}/chat/completions"
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=UPSTREAM_TIMEOUT_SECONDS
            ) as client:
                reply = await client.post(url, json=body, headers=self._headers())
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
            tokens_limit=self._settings.agent_model_tokens_per_day,
        )


def get_model_service(
    repo: Annotated[AgentRepository, Depends(get_agent_repository)],
    clock: Annotated[Clock, Depends(get_clock)],
    settings: Annotated[Settings, Depends(get_settings)],
    transport: Annotated[httpx.AsyncBaseTransport | None, Depends(get_upstream_transport)],
) -> ModelService:
    return ModelService(repo, clock, settings, transport)
