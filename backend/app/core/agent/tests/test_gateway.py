"""The model gateway, against a fake upstream: allowlist, limits, forwarding and usage counts."""

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import anyio
import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.config import get_settings as real_get_settings
from app.core.agent import get_upstream_transport
from app.core.agent.model_service import ModelService
from app.core.agent.repository import AgentRepository
from app.shared.db import session_factory
from tests.fakes import FakeIdentity, MovableClock
from tests.support import bearer, cli_sign_in, sign_in

MARCUS = "marcus.lee@fernhill.example"
UPSTREAM = "http://upstream.test/v1"
CHAT = {"model": "garden-small", "messages": [{"role": "user", "content": "hello"}]}


class FakeUpstream:
    """An OpenAI-compatible upstream that records what it was sent and answers as configured."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.usage: dict[str, int] = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "upstream trouble"})
        sent: dict[str, Any] = json.loads(request.content)
        if sent.get("stream") is True:
            events: list[dict[str, Any]] = [{"choices": [{"delta": {"content": "pong"}}]}]
            options: dict[str, Any] = sent.get("stream_options") or {}
            if options.get("include_usage") is True:
                events.append({"choices": [], "usage": self.usage})
            body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
            return httpx.Response(
                200,
                content=(body + "data: [DONE]\n\n").encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(
            200,
            json={"object": "chat.completion", "choices": [], "usage": self.usage},
        )


@pytest.fixture
def upstream() -> FakeUpstream:
    return FakeUpstream()


@pytest.fixture
def configure(app: FastAPI, upstream: FakeUpstream) -> Callable[..., None]:
    """Point the gateway at the fake upstream, with the given settings changed."""

    def configure(**changes: Any) -> None:
        settings = real_get_settings().model_copy(
            update={
                "agent_model_upstream_url": UPSTREAM,
                "agent_model_api_key": SecretStr("test-key"),
                "agent_model_models": "garden-small,garden-large",
                "agent_model_requests_per_minute": 20,
                "agent_model_tokens_per_day": 200_000,
                **changes,
            }
        )
        app.dependency_overrides[real_get_settings] = lambda: settings
        app.dependency_overrides[get_upstream_transport] = lambda: httpx.MockTransport(upstream)

    return configure


@pytest.mark.asyncio
async def test_models_lists_the_allowlist(
    client: AsyncClient, identity: FakeIdentity, configure: Callable[..., None]
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)
    listed = await client.get("/api/agent/model/v1/models")
    assert listed.status_code == 200, listed.text
    assert [model["id"] for model in listed.json()["data"]] == ["garden-large", "garden-small"]


@pytest.mark.asyncio
async def test_completion_is_forwarded_with_the_key_and_counted(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)
    answered = await client.post("/api/agent/model/v1/chat/completions", json=CHAT)
    assert answered.status_code == 200, answered.text
    assert answered.json()["object"] == "chat.completion"

    [sent] = upstream.requests
    assert str(sent.url) == f"{UPSTREAM}/chat/completions"
    assert sent.headers["authorization"] == "Bearer test-key"
    assert json.loads(sent.content) == CHAT

    today = (await client.get("/api/agent/model/my-usage")).json()
    assert today["requests"] == 1
    assert today["total_tokens"] == 7
    assert today["tokens_limit"] == 200_000
    assert set(today) == {
        "day",
        "requests",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "tokens_limit",
    }


@pytest.mark.asyncio
async def test_unconfigured_upstream_answers_503(
    client: AsyncClient, identity: FakeIdentity, configure: Callable[..., None]
) -> None:
    configure(agent_model_upstream_url="")
    await sign_in(client, identity, MARCUS)
    response = await client.post("/api/agent/model/v1/chat/completions", json=CHAT)
    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_a_model_off_the_allowlist_is_refused(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)
    response = await client.post(
        "/api/agent/model/v1/chat/completions", json={**CHAT, "model": "other-model"}
    )
    assert response.status_code == 403
    assert upstream.requests == []


@pytest.mark.asyncio
async def test_a_stream_is_relayed_unchanged_and_counted(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)
    streamed = await client.post(
        "/api/agent/model/v1/chat/completions", json={**CHAT, "stream": True}
    )
    assert streamed.status_code == 200, streamed.text
    assert streamed.headers["content-type"].startswith("text/event-stream")
    assert streamed.text.endswith("data: [DONE]\n\n")
    assert '"content": "pong"' in streamed.text

    [sent] = upstream.requests
    assert json.loads(sent.content) == {
        **CHAT,
        "stream": True,
        "stream_options": {"include_usage": True},
    }

    today = (await client.get("/api/agent/model/my-usage")).json()
    assert today["requests"] == 1
    assert today["prompt_tokens"] == 3
    assert today["completion_tokens"] == 4
    assert today["total_tokens"] == 7


@pytest.mark.asyncio
async def test_a_stream_keeps_the_client_stream_options_and_asks_for_usage(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)
    streamed = await client.post(
        "/api/agent/model/v1/chat/completions",
        json={**CHAT, "stream": True, "stream_options": {"other": 1, "include_usage": False}},
    )
    assert streamed.status_code == 200, streamed.text

    [sent] = upstream.requests
    assert json.loads(sent.content)["stream_options"] == {"other": 1, "include_usage": True}
    today = (await client.get("/api/agent/model/my-usage")).json()
    assert today["total_tokens"] == 7


@pytest.mark.asyncio
async def test_a_stream_whose_upstream_fails_is_502_and_counted(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
) -> None:
    configure()
    upstream.status = 500
    await sign_in(client, identity, MARCUS)
    response = await client.post(
        "/api/agent/model/v1/chat/completions", json={**CHAT, "stream": True}
    )
    assert response.status_code == 502
    today = (await client.get("/api/agent/model/my-usage")).json()
    assert today["requests"] == 1
    assert today["total_tokens"] == 0


@pytest.mark.asyncio
async def test_each_cli_login_is_limited_per_minute_and_the_window_slides(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    clock: MovableClock,
) -> None:
    configure(agent_model_requests_per_minute=2)
    token = await cli_sign_in(client, identity, MARCUS)
    for _ in range(2):
        ok = await client.post(
            "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
        )
        assert ok.status_code == 200, ok.text
    limited = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"

    clock.advance(timedelta(seconds=61))
    again = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )
    assert again.status_code == 200, again.text


@pytest.mark.asyncio
async def test_a_person_is_stopped_at_the_daily_token_allowance(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
    clock: MovableClock,
) -> None:
    configure(agent_model_tokens_per_day=10)
    upstream.usage = {"prompt_tokens": 6, "completion_tokens": 6, "total_tokens": 12}
    token = await cli_sign_in(client, identity, MARCUS)
    first = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )
    assert first.status_code == 200, first.text
    stopped = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )
    assert stopped.status_code == 429

    clock.advance(timedelta(days=1))
    tomorrow = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )
    assert tomorrow.status_code == 200, tomorrow.text


@pytest.mark.asyncio
async def test_upstream_failure_is_502_and_counts_a_request_with_no_tokens(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
) -> None:
    configure()
    upstream.status = 500
    await sign_in(client, identity, MARCUS)
    failed = await client.post("/api/agent/model/v1/chat/completions", json=CHAT)
    assert failed.status_code == 502
    today = (await client.get("/api/agent/model/my-usage")).json()
    assert today["requests"] == 1
    assert today["total_tokens"] == 0


@pytest.mark.asyncio
async def test_models_names_the_configured_default_beside_the_list(
    client: AsyncClient, identity: FakeIdentity, configure: Callable[..., None]
) -> None:
    configure(agent_model_default="garden-large")
    await sign_in(client, identity, MARCUS)

    listed = await client.get("/api/agent/model/v1/models")

    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["object"] == "list"
    assert [model["id"] for model in body["data"]] == ["garden-large", "garden-small"]
    assert body["default"] == "garden-large"


@pytest.mark.asyncio
async def test_default_is_the_first_allowed_model_when_none_is_configured(
    client: AsyncClient, identity: FakeIdentity, configure: Callable[..., None]
) -> None:
    configure(agent_model_models="garden-small,garden-large")
    await sign_in(client, identity, MARCUS)

    body = (await client.get("/api/agent/model/v1/models")).json()

    assert body["default"] == "garden-small"
    assert [model["id"] for model in body["data"]] == ["garden-large", "garden-small"]


@pytest.mark.asyncio
async def test_default_is_null_when_no_model_is_allowed(
    client: AsyncClient, identity: FakeIdentity, configure: Callable[..., None]
) -> None:
    configure(agent_model_models="")
    await sign_in(client, identity, MARCUS)

    body = (await client.get("/api/agent/model/v1/models")).json()

    assert body == {"object": "list", "data": [], "default": None}


@pytest.mark.asyncio
async def test_default_is_not_an_alias_for_chat(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
) -> None:
    configure(agent_model_default="garden-large")
    await sign_in(client, identity, MARCUS)

    answered = await client.post(
        "/api/agent/model/v1/chat/completions", json={**CHAT, "model": "default"}
    )

    assert answered.status_code == 403
    assert upstream.requests == []


def test_the_default_model_must_be_on_the_allowlist() -> None:
    with pytest.raises(ValueError, match="GARDEN_AGENT_MODEL_DEFAULT"):
        _ = Settings(
            database_url="postgresql://unused",
            agent_model_models="garden-small",
            agent_model_default="garden-large",
        )


class Wire:
    """A scripted upstream stream: each item is one `data:` event, sent as written."""

    def __init__(self, events: list[dict[str, Any]], *, hold: bool = False) -> None:
        self.events = events
        self.hold = hold
        self.requests = 0

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if self.hold:
            await anyio.sleep_forever()
        sent: dict[str, Any] = json.loads(request.content)
        if sent.get("stream") is True:
            body = "".join(f"data: {json.dumps(event)}\n\n" for event in self.events)
            return httpx.Response(
                200,
                content=(body + "data: [DONE]\n\n").encode(),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json={"choices": [], **self.events[-1]})


async def rows(person_id: uuid.UUID) -> tuple[int, int, int, int]:
    async with session_factory(real_get_settings().database_url)() as session:
        return await AgentRepository(session).totals_since(
            person_id=person_id, since=datetime(2000, 1, 1, tzinfo=UTC)
        )


def service_on(wire: Wire, session: Any) -> ModelService:
    settings = real_get_settings().model_copy(
        update={
            "agent_model_upstream_url": UPSTREAM,
            "agent_model_models": "garden-small",
            "agent_model_requests_per_minute": 1000,
        }
    )
    return ModelService(
        AgentRepository(session),
        MovableClock(datetime(2026, 11, 3, 9, 0, tzinfo=UTC)),
        settings,
        httpx.MockTransport(wire),
    )


async def abandon_after_first_event(events: AsyncIterator[bytes]) -> None:
    """Read one event and then drop the connection, as a client that is killed does.
    The server closes the abandoned stream, which is what this does explicitly."""
    with anyio.CancelScope() as scope:
        async for _ in events:
            scope.cancel()
            await anyio.sleep_forever()
    await cast("AsyncGenerator[bytes, None]", events).aclose()


USAGE = {"prompt_tokens": 11, "completion_tokens": 5, "total_tokens": 16}
CONTENT = {"choices": [{"delta": {"content": "x"}}]}


@pytest.mark.asyncio
async def test_a_stream_the_client_drops_still_writes_its_row_with_the_upstream_usage(
    clean_database: None,
) -> None:
    person = uuid.uuid4()
    wire = Wire([CONTENT, CONTENT, {"choices": [], "usage": USAGE}])
    async with session_factory(real_get_settings().database_url)() as session:
        events = await service_on(wire, session).stream(
            person_id=person, cli_login_id=None, body={**CHAT, "stream": True}
        )
        await abandon_after_first_event(events)
    assert await rows(person) == (1, 11, 5, 16)


@pytest.mark.asyncio
async def test_a_call_cancelled_while_the_upstream_thinks_still_writes_its_row(
    clean_database: None,
) -> None:
    person = uuid.uuid4()
    wire = Wire([], hold=True)
    async with session_factory(real_get_settings().database_url)() as session:
        with anyio.move_on_after(0.2):
            await service_on(wire, session).complete(person_id=person, cli_login_id=None, body=CHAT)
    assert wire.requests == 1
    assert await rows(person) == (1, 0, 0, 0)


@pytest.mark.asyncio
async def test_stream_usage_is_counted_when_it_rides_with_content_or_arrives_in_pieces(
    clean_database: None,
) -> None:
    person = uuid.uuid4()
    wire = Wire(
        [
            {**CONTENT, "usage": {"prompt_tokens": 11}},
            {**CONTENT, "usage": {"completion_tokens": 5}},
        ]
    )
    async with session_factory(real_get_settings().database_url)() as session:
        service = service_on(wire, session)
        body = {**CHAT, "stream": True}
        async for _ in await service.stream(person_id=person, cli_login_id=None, body=body):
            pass
    assert await rows(person) == (1, 11, 5, 16)
