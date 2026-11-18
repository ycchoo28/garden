"""The model gateway, against a fake upstream: allowlist, limits, forwarding and usage counts."""

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import anyio
import httpx
import pytest
from fastapi import FastAPI
from garden.trace_contract import (
    AGENT_CONVERSATION_HEADER,
    GARDEN_AGENT_CONVERSATION_ID,
    GARDEN_AGENT_LOGIN_ID,
    GARDEN_CONTENT_CAPTURED,
    GARDEN_CONVERSATION_TITLE_SIDE_CALL,
    GARDEN_EVAL_RUN_ID,
    GARDEN_EVAL_SIDE_CALL,
    GARDEN_EVAL_SIDE_CALL_HEADER,
    GARDEN_EVAL_TRIAL_ID,
    GARDEN_MODEL_RUNTIME_CONTEXT,
    GARDEN_MODEL_SIDE_CALL_INPUT_MESSAGES,
    GARDEN_MODEL_SIDE_CALL_OUTPUT_MESSAGES,
    GARDEN_MODEL_SIDE_CALL_TYPE,
    GARDEN_MODEL_TOTAL_TOKENS,
    GARDEN_PERSON_ID,
    GEN_AI_CAPTURE_CONTENT_ENV,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_CUSTOM_PROVIDER,
    GEN_AI_INFERENCE_EVENT,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_STATUS,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    MODEL_SIDE_CALL_HEADER,
)
from httpx import AsyncClient
from opentelemetry import _logs, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
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


def _span_attributes(span: Any) -> Mapping[str, Any]:
    attributes: object = span.attributes
    assert isinstance(attributes, Mapping)
    return cast("Mapping[str, Any]", attributes)


def _model_gateway_spans(exporter: InMemorySpanExporter) -> list[Any]:
    return [
        span
        for span in exporter.get_finished_spans()
        if GEN_AI_PROVIDER_NAME in _span_attributes(span)
    ]


def _log_event_attributes(
    exporter: InMemoryLogRecordExporter, name: str
) -> list[Mapping[str, Any]]:
    attributes: list[Mapping[str, Any]] = []
    for record in exporter.get_finished_logs():
        log_record = record.log_record
        if log_record.event_name != name:
            continue
        event_attributes: object = log_record.attributes
        assert isinstance(event_attributes, Mapping)
        attributes.append(cast("Mapping[str, Any]", event_attributes))
    return attributes


class FakeUpstream:
    """An OpenAI-compatible upstream that records what it was sent and answers as configured."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.status = 200
        self.usage: dict[str, int] = {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}
        self.stream_lines: list[str] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "upstream trouble"})
        sent: dict[str, Any] = json.loads(request.content)
        if sent.get("stream") is True:
            if self.stream_lines is not None:
                return httpx.Response(
                    200,
                    content="".join(self.stream_lines).encode(),
                    headers={"content-type": "text/event-stream"},
                )
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
            json={
                "object": "chat.completion",
                "model": sent["model"],
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "pong"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": self.usage,
            },
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


@pytest.fixture
def span_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "get_tracer", provider.get_tracer)
    yield exporter
    provider.shutdown()


@pytest.fixture
def log_exporter(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemoryLogRecordExporter]:
    provider = LoggerProvider()
    exporter = InMemoryLogRecordExporter()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr(_logs, "get_logger", provider.get_logger)
    yield exporter
    provider.shutdown()


@pytest.mark.asyncio
async def test_model_calls_emit_contract_spans_with_usage_matching_the_usage_rows(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
    span_exporter: InMemorySpanExporter,
    log_exporter: InMemoryLogRecordExporter,
) -> None:
    configure(eval_run_id="eval-42", eval_trial_id="trial-42")
    token = await cli_sign_in(client, identity, MARCUS)

    for streaming in (False, True):
        answered = await client.post(
            "/api/agent/model/v1/chat/completions",
            json={**CHAT, "stream": streaming},
            headers={**bearer(token), AGENT_CONVERSATION_HEADER: "conversation-42"},
        )
        assert answered.status_code == 200, answered.text

    usage = (await client.get("/api/agent/model/my-usage", headers=bearer(token))).json()
    spans = _model_gateway_spans(span_exporter)
    attributes = [_span_attributes(span) for span in spans]

    assert len(spans) == 2
    assert all(span.name == "chat" for span in spans)
    assert all(span.kind == SpanKind.CLIENT for span in spans)
    assert {attrs[GEN_AI_OPERATION_NAME] for attrs in attributes} == {"chat"}
    assert {attrs[GEN_AI_PROVIDER_NAME] for attrs in attributes} == {GEN_AI_CUSTOM_PROVIDER}
    assert {attrs[GEN_AI_REQUEST_MODEL] for attrs in attributes} == {"garden-small"}
    assert {attrs[GEN_AI_RESPONSE_MODEL] for attrs in attributes} == {"garden-small"}
    assert {attrs[GEN_AI_RESPONSE_STATUS] for attrs in attributes} == {"completed"}
    assert all(attrs[GARDEN_PERSON_ID] for attrs in attributes)
    assert all(attrs[GARDEN_AGENT_LOGIN_ID] for attrs in attributes)
    assert {attrs[GARDEN_EVAL_RUN_ID] for attrs in attributes} == {"eval-42"}
    assert {attrs[GARDEN_EVAL_TRIAL_ID] for attrs in attributes} == {"trial-42"}
    assert all(attrs[GARDEN_CONTENT_CAPTURED] is False for attrs in attributes)
    assert {attrs[GARDEN_AGENT_CONVERSATION_ID] for attrs in attributes} == {"conversation-42"}
    for span in spans:
        assert span.start_time is not None
        assert span.end_time is not None
        assert span.start_time < span.end_time
    assert sum(attrs[GEN_AI_USAGE_INPUT_TOKENS] for attrs in attributes) == usage["prompt_tokens"]
    assert (
        sum(attrs[GEN_AI_USAGE_OUTPUT_TOKENS] for attrs in attributes) == usage["completion_tokens"]
    )
    assert sum(attrs[GARDEN_MODEL_TOTAL_TOKENS] for attrs in attributes) == usage["total_tokens"]
    assert all("hello" not in str(attrs) for attrs in attributes)
    assert all("pong" not in str(attrs) for attrs in attributes)
    assert not log_exporter.get_finished_logs()


@pytest.mark.asyncio
async def test_content_capture_records_messages_only_when_enabled_in_dev(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
) -> None:
    configure(gen_ai_capture_content="SPAN_ONLY")
    await sign_in(client, identity, MARCUS)

    answered = await client.post("/api/agent/model/v1/chat/completions", json=CHAT)

    assert answered.status_code == 200, answered.text
    [span] = _model_gateway_spans(span_exporter)
    attributes = _span_attributes(span)
    assert attributes[GARDEN_CONTENT_CAPTURED] is True
    assert "hello" in attributes[GEN_AI_INPUT_MESSAGES]
    assert "pong" in attributes[GEN_AI_OUTPUT_MESSAGES]


@pytest.mark.parametrize(
    ("capture_mode", "streaming"), [("EVENT_ONLY", False), ("SPAN_AND_EVENT", True)]
)
@pytest.mark.asyncio
async def test_content_capture_event_modes_follow_the_configured_signal(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
    log_exporter: InMemoryLogRecordExporter,
    capture_mode: str,
    streaming: bool,
) -> None:
    configure(gen_ai_capture_content=capture_mode)
    await sign_in(client, identity, MARCUS)

    answered = await client.post(
        "/api/agent/model/v1/chat/completions", json={**CHAT, "stream": streaming}
    )

    assert answered.status_code == 200, answered.text
    [span] = _model_gateway_spans(span_exporter)
    attributes = _span_attributes(span)
    [log_record] = log_exporter.get_finished_logs()
    assert log_record.log_record.event_name == GEN_AI_INFERENCE_EVENT
    assert log_record.log_record.severity_number == SeverityNumber.DEBUG
    [event_attributes] = _log_event_attributes(log_exporter, GEN_AI_INFERENCE_EVENT)
    assert attributes[GARDEN_CONTENT_CAPTURED] is True
    assert event_attributes[GEN_AI_OPERATION_NAME] == "chat"
    assert event_attributes[GEN_AI_PROVIDER_NAME] == GEN_AI_CUSTOM_PROVIDER
    assert "hello" in json.dumps(event_attributes[GEN_AI_INPUT_MESSAGES])
    assert "pong" in json.dumps(event_attributes[GEN_AI_OUTPUT_MESSAGES])
    assert (GEN_AI_INPUT_MESSAGES in attributes) is (capture_mode == "SPAN_AND_EVENT")
    assert (GEN_AI_OUTPUT_MESSAGES in attributes) is (capture_mode == "SPAN_AND_EVENT")


def test_content_capture_is_rejected_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GARDEN_FAKE_OIDC", "false")
    with pytest.raises(ValueError, match="OTEL_INSTRUMENTATION_GENAI_CAPTURE_CONTENT"):
        Settings(
            database_url="postgresql://unused/garden_test",
            environment="prod",
            public_url="https://garden.example",
            gen_ai_capture_content="SPAN_ONLY",
        )


def test_content_capture_reads_the_standard_otel_environment_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GEN_AI_CAPTURE_CONTENT_ENV, "span_only")

    settings = Settings(database_url="postgresql://unused/garden_test")

    assert settings.gen_ai_capture_content == "SPAN_ONLY"


@pytest.mark.asyncio
async def test_gateway_tags_a_recognizable_conversation_title_side_call(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)

    answered = await client.post(
        "/api/agent/model/v1/chat/completions",
        json=CHAT,
        headers={MODEL_SIDE_CALL_HEADER: "conversation_title"},
    )

    assert answered.status_code == 200, answered.text
    [span] = _model_gateway_spans(span_exporter)
    assert (
        _span_attributes(span)[GARDEN_MODEL_SIDE_CALL_TYPE] == GARDEN_CONVERSATION_TITLE_SIDE_CALL
    )


@pytest.mark.asyncio
async def test_gateway_and_api_trace_tag_eval_harness_side_calls(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)

    answered = await client.post(
        "/api/agent/model/v1/chat/completions",
        json=CHAT,
        headers={
            MODEL_SIDE_CALL_HEADER: "eval_grader",
            GARDEN_EVAL_SIDE_CALL_HEADER: "trial_metrics",
        },
    )

    assert answered.status_code == 200, answered.text
    [chat_span] = _model_gateway_spans(span_exporter)
    assert _span_attributes(chat_span)[GARDEN_MODEL_SIDE_CALL_TYPE] == "eval_grader"
    [api_span] = [
        span
        for span in span_exporter.get_finished_spans()
        if span.name == "agent.model.chat_completion"
    ]
    assert _span_attributes(api_span)[GARDEN_EVAL_SIDE_CALL] == "trial_metrics"


@pytest.mark.asyncio
async def test_upstream_failure_is_recorded_as_an_error_span(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    upstream: FakeUpstream,
    span_exporter: InMemorySpanExporter,
) -> None:
    configure()
    upstream.status = 500
    token = await cli_sign_in(client, identity, MARCUS)

    failed = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )

    assert failed.status_code == 502
    [span] = _model_gateway_spans(span_exporter)
    attributes = _span_attributes(span)
    assert attributes[GEN_AI_RESPONSE_STATUS] == "failed"
    assert span.status.status_code == trace.StatusCode.ERROR
    assert attributes[GEN_AI_USAGE_INPUT_TOKENS] == 0
    assert attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 0
    assert "hello" not in str(attributes)


@pytest.mark.asyncio
async def test_inferred_agent_conversation_reuses_until_an_idle_gap(
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    clock: MovableClock,
    span_exporter: InMemorySpanExporter,
) -> None:
    configure()
    token = await cli_sign_in(client, identity, MARCUS)

    first = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )
    second = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )
    clock.advance(timedelta(minutes=31))
    third = await client.post(
        "/api/agent/model/v1/chat/completions", json=CHAT, headers=bearer(token)
    )

    assert first.status_code == second.status_code == third.status_code == 200
    [earlier, continuing, later] = _model_gateway_spans(span_exporter)
    earlier_id = _span_attributes(earlier)[GARDEN_AGENT_CONVERSATION_ID]
    continuing_id = _span_attributes(continuing)[GARDEN_AGENT_CONVERSATION_ID]
    later_id = _span_attributes(later)[GARDEN_AGENT_CONVERSATION_ID]
    assert earlier_id == continuing_id
    assert later_id != continuing_id


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


@pytest.mark.parametrize("role", ["system", "developer"])
@pytest.mark.parametrize("parts", [False, True])
@pytest.mark.asyncio
async def test_dsh_title_request_has_its_own_thread_without_changing_upstream_prompt(
    upstream: FakeUpstream,
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
    role: str,
    parts: bool,
) -> None:
    configure()
    await sign_in(client, identity, MARCUS)
    title_body: dict[str, Any] = {
        **CHAT,
        "messages": [
            {
                "role": role,
                "content": (
                    "Create a concise title for an AI coding-assistant session "
                    "from the supplied human messages."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Generate the session title from this JSON array of human messages:\n"
                    '[{"seq":8,"text":"Draft a claim."}]'
                ),
            },
        ],
    }
    if parts:
        for message in cast(list[dict[str, Any]], title_body["messages"]):
            message["content"] = [{"type": "text", "text": message["content"]}]
    response = await client.post("/api/agent/model/v1/chat/completions", json=title_body)
    assert response.status_code == 200
    [span] = _model_gateway_spans(span_exporter)
    attributes = _span_attributes(span)
    assert json.loads(upstream.requests[-1].content)["messages"] == title_body["messages"]
    assert span.name == "chat conversation_title"
    assert attributes[GARDEN_MODEL_SIDE_CALL_TYPE] == GARDEN_CONVERSATION_TITLE_SIDE_CALL
    assert attributes[GEN_AI_CONVERSATION_ID] == (
        str(attributes[GARDEN_AGENT_CONVERSATION_ID]) + ":title"
    )


@pytest.mark.asyncio
async def test_title_side_call_keeps_messages_out_of_thread_payload_and_api_span_not_tool_call(
    upstream: FakeUpstream,
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
) -> None:
    configure(gen_ai_capture_content="SPAN_ONLY")
    token = await cli_sign_in(client, identity, MARCUS, device="cli")
    title_body: dict[str, Any] = {
        **CHAT,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Create a concise title for an AI coding-assistant session "
                    "from the supplied human messages."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Generate the session title from this JSON array of human messages:\n"
                    '[{"seq":1,"text":"Draft a claim."}]'
                ),
            },
        ],
    }

    response = await client.post(
        "/api/agent/model/v1/chat/completions",
        json=title_body,
        headers=bearer(token),
    )

    assert response.status_code == 200
    [chat_span] = _model_gateway_spans(span_exporter)
    chat_attrs = _span_attributes(chat_span)
    assert GEN_AI_INPUT_MESSAGES not in chat_attrs
    assert GEN_AI_OUTPUT_MESSAGES not in chat_attrs
    assert "Draft a claim." in chat_attrs[GARDEN_MODEL_SIDE_CALL_INPUT_MESSAGES]
    assert "pong" in chat_attrs[GARDEN_MODEL_SIDE_CALL_OUTPUT_MESSAGES]
    [api_span] = [
        span
        for span in span_exporter.get_finished_spans()
        if span.name == "agent.model.chat_completion"
    ]
    assert GEN_AI_OPERATION_NAME not in _span_attributes(api_span)


@pytest.mark.asyncio
async def test_streaming_tool_calls_are_captured_in_output_messages(
    upstream: FakeUpstream,
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
) -> None:
    configure(gen_ai_capture_content="SPAN_ONLY")
    await sign_in(client, identity, MARCUS)
    chunk_one = {
        "id": "chatcmpl-1",
        "model": "gpt-6-luna",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "claims_claim_create",
                                "arguments": '{"title":',
                            },
                        }
                    ],
                },
                "finish_reason": None,
            }
        ],
    }
    chunk_two = {
        "id": "chatcmpl-1",
        "model": "gpt-6-luna",
        "choices": [
            {
                "index": 0,
                "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '"Dinner"}'}}]},
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    upstream.stream_lines = [
        f"data: {json.dumps(chunk_one)}\n\n",
        f"data: {json.dumps(chunk_two)}\n\n",
        "data: [DONE]\n\n",
    ]

    response = await client.post(
        "/api/agent/model/v1/chat/completions",
        json={**CHAT, "stream": True},
    )

    assert response.status_code == 200
    [chat_span] = _model_gateway_spans(span_exporter)
    attrs = _span_attributes(chat_span)
    output = json.loads(str(attrs[GEN_AI_OUTPUT_MESSAGES]))
    assert output == [
        {
            "role": "assistant",
            "parts": [
                {
                    "type": "tool_call",
                    "id": "call_1",
                    "name": "claims_claim_create",
                    "arguments": {"title": "Dinner"},
                }
            ],
        }
    ]


@pytest.mark.asyncio
async def test_agent_tool_http_span_captures_arguments_and_result_when_enabled(
    client: AsyncClient,
    identity: FakeIdentity,
    monkeypatch: pytest.MonkeyPatch,
    span_exporter: InMemorySpanExporter,
) -> None:
    monkeypatch.setenv(GEN_AI_CAPTURE_CONTENT_ENV, "SPAN_ONLY")
    token = await cli_sign_in(client, identity, MARCUS, device="cli")

    created = await client.post(
        "/api/claims",
        json={"title": "Client dinner"},
        headers=bearer(token),
    )

    assert created.status_code == 201, created.text
    [tool_span] = [
        span for span in span_exporter.get_finished_spans() if span.name == "claims.claim.create"
    ]
    attrs = _span_attributes(tool_span)
    assert attrs[GEN_AI_OPERATION_NAME] == "execute_tool"
    assert attrs[GEN_AI_TOOL_NAME] == "claims.claim.create"
    assert attrs[GARDEN_CONTENT_CAPTURED] is True
    assert json.loads(str(attrs[GEN_AI_TOOL_CALL_ARGUMENTS])) == {
        "body": {"title": "Client dinner"}
    }
    assert json.loads(str(attrs[GEN_AI_INPUT_MESSAGES])) == {"body": {"title": "Client dinner"}}
    result_payload = json.loads(str(attrs[GEN_AI_TOOL_CALL_RESULT]))
    assert result_payload["claim"]["title"] == "Client dinner"
    assert json.loads(str(attrs[GEN_AI_OUTPUT_MESSAGES])) == result_payload


@pytest.mark.asyncio
async def test_runtime_context_snapshot_is_extracted_from_human_thread_messages(
    upstream: FakeUpstream,
    client: AsyncClient,
    identity: FakeIdentity,
    configure: Callable[..., None],
    span_exporter: InMemorySpanExporter,
) -> None:
    configure(gen_ai_capture_content="SPAN_ONLY")
    await sign_in(client, identity, MARCUS)
    runtime_text = (
        "Current runtime context. This snapshot supersedes earlier runtime-context snapshots.\n\n"
        'Current working directory: "/tmp/workspace".'
    )
    body = {
        "model": "garden-small",
        "messages": [
            {"role": "developer", "content": "You are an assistant."},
            {"role": "user", "content": "Draft a claim for lunch."},
            {"role": "user", "content": runtime_text},
        ],
    }

    response = await client.post("/api/agent/model/v1/chat/completions", json=body)

    assert response.status_code == 200
    assert json.loads(upstream.requests[-1].content)["messages"] == body["messages"]
    [chat_span] = _model_gateway_spans(span_exporter)
    attrs = _span_attributes(chat_span)
    traced_input = json.loads(str(attrs[GEN_AI_INPUT_MESSAGES]))
    assert traced_input == [
        {"role": "system", "parts": [{"type": "text", "content": "You are an assistant."}]},
        {"role": "user", "parts": [{"type": "text", "content": "Draft a claim for lunch."}]},
    ]
    assert attrs[GARDEN_MODEL_RUNTIME_CONTEXT] == runtime_text
