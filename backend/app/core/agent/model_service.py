"""The model gateway: forwards OpenAI-compatible chat requests and counts usage only."""

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from threading import Lock
from typing import Annotated, Any, cast

import anyio
import httpx
from fastapi import Depends
from garden.trace_contract import (
    GARDEN_AGENT_CONVERSATION_ID,
    GARDEN_AGENT_LOGIN_ID,
    GARDEN_CONTENT_CAPTURED,
    GARDEN_CONVERSATION_TITLE_SIDE_CALL,
    GARDEN_EVAL_RUN_ID,
    GARDEN_EVAL_TRIAL_ID,
    GARDEN_MODEL_SIDE_CALL_TYPE,
    GARDEN_MODEL_TOOL_NAMES,
    GARDEN_MODEL_TOTAL_TOKENS,
    GARDEN_PERSON_ID,
    GEN_AI_CAPTURE_EVENT_MODES,
    GEN_AI_CAPTURE_SPAN_MODES,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_CUSTOM_PROVIDER,
    GEN_AI_INFERENCE_EVENT,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_STREAM,
    GEN_AI_RESPONSE_ID,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_RESPONSE_STATUS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
)
from opentelemetry import _logs, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

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
AGENT_CONVERSATION_IDLE_GAP = timedelta(minutes=30)
_agent_conversations: dict[tuple[uuid.UUID, uuid.UUID | None], tuple[str, datetime]] = {}
_agent_conversations_lock = Lock()


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


def _usage_counts(usage: dict[str, Any]) -> tuple[int, int, int]:
    prompt = _count(usage.get("prompt_tokens"))
    completion = _count(usage.get("completion_tokens"))
    total = _count(usage.get("total_tokens")) or prompt + completion
    return prompt, completion, total


def _conversation_id(
    *,
    person_id: uuid.UUID,
    cli_login_id: uuid.UUID | None,
    now: datetime,
    supplied: str | None,
) -> str:
    if supplied and len(supplied) > 128:
        supplied = None
    key = (person_id, cli_login_id)
    with _agent_conversations_lock:
        expired = [
            login
            for login, (_, last_seen) in _agent_conversations.items()
            if now - last_seen > AGENT_CONVERSATION_IDLE_GAP
        ]
        for login in expired:
            _agent_conversations.pop(login, None)
        previous = _agent_conversations.get(key)
        if supplied:
            conversation = supplied
        elif previous is not None and now - previous[1] <= AGENT_CONVERSATION_IDLE_GAP:
            conversation = previous[0]
        else:
            conversation = str(uuid.uuid4())
        _agent_conversations[key] = (conversation, now)
        return conversation


def _content_parts(content: object) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "content": content}]
    if not isinstance(content, list):
        return []
    parts: list[dict[str, Any]] = []
    for raw_part in cast("list[object]", content):
        part = raw_part
        if isinstance(part, str):
            parts.append({"type": "text", "content": part})
        elif isinstance(part, dict):
            part = cast("dict[str, Any]", part)
            if part.get("type") == "text":
                text = part.get("text", part.get("content"))
                if isinstance(text, str):
                    parts.append({"type": "text", "content": text})
            else:
                parts.append(part)
    return parts


def _is_conversation_title_request(body: dict[str, Any]) -> bool:
    """Recognize the bundled DSH title request for telemetry classification only."""
    messages = body.get("messages")
    if body.get("tools") or not isinstance(messages, list):
        return False
    messages = cast("list[object]", messages)
    if len(messages) != 2:
        return False
    system, user = messages
    if not isinstance(system, dict) or not isinstance(user, dict):
        return False
    system = cast("dict[str, Any]", system)
    user = cast("dict[str, Any]", user)
    instruction = "\n".join(
        str(part.get("content", "")) for part in _content_parts(system.get("content"))
    )
    content = "\n".join(
        str(part.get("content", "")) for part in _content_parts(user.get("content"))
    )
    prefix = "Generate the session title from this JSON array of human messages:\n"
    return (
        system.get("role") in {"system", "developer"}
        and instruction.startswith(
            "Create a concise title for an AI coding-assistant session "
            "from the supplied human messages."
        )
        and user.get("role") == "user"
        and content.startswith((prefix, "Update the session title from this JSON object:\n"))
    )


def _trace_messages(messages: object) -> list[dict[str, Any]] | None:
    if not isinstance(messages, list):
        return None
    result: list[dict[str, Any]] = []
    for raw_message in cast("list[object]", messages):
        if not isinstance(raw_message, dict):
            continue
        message = cast("dict[str, Any]", raw_message)
        role = message.get("role")
        traced: dict[str, Any] = {
            "role": role if isinstance(role, str) else "unknown",
            "parts": _content_parts(message.get("content")),
        }
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw_call in cast("list[object]", tool_calls):
                if not isinstance(raw_call, dict):
                    continue
                call = cast("dict[str, Any]", raw_call)
                function = call.get("function")
                if not isinstance(function, dict):
                    continue
                function = cast("dict[str, Any]", function)
                arguments: object = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {"value": arguments}
                traced["parts"].append(
                    {
                        "type": "tool_call",
                        "id": call.get("id", ""),
                        "name": function.get("name", ""),
                        "arguments": arguments,
                    }
                )
        result.append(traced)
    return result


def _tool_names(messages: object) -> list[str]:
    traced = _trace_messages(messages)
    if traced is None:
        return []
    names: list[str] = []
    for message in traced:
        for part in message["parts"]:
            name = part.get("name")
            if part.get("type") == "tool_call" and isinstance(name, str) and name not in names:
                names.append(name)
    return names


def _response_messages(response: dict[str, Any]) -> list[dict[str, Any]]:
    choices = response.get("choices")
    if not isinstance(choices, list):
        return []
    result: list[dict[str, Any]] = []
    for raw_choice in cast("list[object]", choices):
        if not isinstance(raw_choice, dict):
            continue
        choice = cast("dict[str, Any]", raw_choice)
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        message = cast("dict[str, Any]", message)
        result.extend(_trace_messages([message]) or [])
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str):
            result[-1]["finish_reason"] = finish_reason
    return result


def _stream_delta(line: str) -> tuple[str, list[str]]:
    if not line.startswith("data:"):
        return "", []
    try:
        event: object = json.loads(line[len("data:") :])
    except ValueError:
        return "", []
    if not isinstance(event, dict):
        return "", []
    event = cast("dict[str, Any]", event)
    choices = event.get("choices")
    if not isinstance(choices, list):
        return "", []
    text = ""
    names: list[str] = []
    for raw_choice in cast("list[object]", choices):
        if not isinstance(raw_choice, dict):
            continue
        choice = cast("dict[str, Any]", raw_choice)
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        delta = cast("dict[str, Any]", delta)
        content = delta.get("content")
        if isinstance(content, str):
            text += content
        tool_calls = delta.get("tool_calls")
        if isinstance(tool_calls, list):
            for raw_call in cast("list[object]", tool_calls):
                if not isinstance(raw_call, dict):
                    continue
                call = cast("dict[str, Any]", raw_call)
                function = call.get("function")
                name = (
                    cast("dict[str, Any]", function).get("name")
                    if isinstance(function, dict)
                    else None
                )
                if isinstance(name, str) and name not in names:
                    names.append(name)
    return text, names


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

    def _start_chat_span(
        self,
        person_id: uuid.UUID,
        cli_login_id: uuid.UUID | None,
        model: str,
        now: datetime,
        body: dict[str, Any],
        supplied_conversation_id: str | None,
        supplied_side_call_type: str | None,
    ) -> tuple[Span, bool, list[dict[str, Any]] | None, dict[str, Any]]:
        metadata = body.get("metadata")
        if not supplied_conversation_id and isinstance(metadata, dict):
            metadata = cast("dict[str, Any]", metadata)
            candidate = metadata.get(GARDEN_AGENT_CONVERSATION_ID)
            if isinstance(candidate, str) and 0 < len(candidate) <= 128:
                supplied_conversation_id = candidate
        conversation_id = _conversation_id(
            person_id=person_id,
            cli_login_id=cli_login_id,
            now=now,
            supplied=supplied_conversation_id,
        )
        messages = _trace_messages(body.get("messages"))
        capture_content = (
            self._settings.gen_ai_capture_content != "NO_CONTENT" and messages is not None
        )
        capture_on_span = self._settings.gen_ai_capture_content in GEN_AI_CAPTURE_SPAN_MODES
        attributes: dict[str, Any] = {
            GEN_AI_OPERATION_NAME: "chat",
            GEN_AI_PROVIDER_NAME: GEN_AI_CUSTOM_PROVIDER,
            GEN_AI_REQUEST_MODEL: model,
            GEN_AI_CONVERSATION_ID: conversation_id,
            GARDEN_PERSON_ID: str(person_id),
            GARDEN_AGENT_CONVERSATION_ID: conversation_id,
            GARDEN_CONTENT_CAPTURED: capture_content,
        }
        if body.get("stream") is True:
            attributes[GEN_AI_REQUEST_STREAM] = True
        if cli_login_id is not None:
            attributes[GARDEN_AGENT_LOGIN_ID] = str(cli_login_id)
        if self._settings.eval_run_id:
            attributes[GARDEN_EVAL_RUN_ID] = self._settings.eval_run_id
        if self._settings.eval_trial_id:
            attributes[GARDEN_EVAL_TRIAL_ID] = self._settings.eval_trial_id
        if capture_content and capture_on_span and messages is not None:
            attributes[GEN_AI_INPUT_MESSAGES] = json.dumps(messages, ensure_ascii=False)

        side_call_type = supplied_side_call_type
        if not side_call_type and isinstance(metadata, dict):
            metadata = cast("dict[str, Any]", metadata)
            candidate = metadata.get(GARDEN_MODEL_SIDE_CALL_TYPE)
            if candidate == GARDEN_CONVERSATION_TITLE_SIDE_CALL or metadata.get("task") in {
                "title",
                "title_generation",
                GARDEN_CONVERSATION_TITLE_SIDE_CALL,
            }:
                side_call_type = GARDEN_CONVERSATION_TITLE_SIDE_CALL
        if not side_call_type and _is_conversation_title_request(body):
            side_call_type = GARDEN_CONVERSATION_TITLE_SIDE_CALL
        if side_call_type in {
            "title",
            "title_generation",
            GARDEN_CONVERSATION_TITLE_SIDE_CALL,
        }:
            attributes[GARDEN_MODEL_SIDE_CALL_TYPE] = GARDEN_CONVERSATION_TITLE_SIDE_CALL
            # OpenObserve groups threads by this semantic attribute. Keep the Garden
            # conversation ID shared for correlation, but separate auxiliary title messages.
            attributes[GEN_AI_CONVERSATION_ID] = f"{conversation_id}:title"
        elif isinstance(side_call_type, str) and side_call_type.strip():
            attributes[GARDEN_MODEL_SIDE_CALL_TYPE] = side_call_type.strip()

        span = trace.get_tracer("garden.model_gateway").start_span(
            "chat conversation_title"
            if attributes.get(GARDEN_MODEL_SIDE_CALL_TYPE) == GARDEN_CONVERSATION_TITLE_SIDE_CALL
            else "chat",
            kind=SpanKind.CLIENT,
            attributes=attributes,
        )
        return span, capture_content, messages, attributes

    def _finish_chat_span(
        self,
        span: Span,
        status: str,
        usage: dict[str, Any],
        model: str,
        *,
        capture_content: bool,
        input_messages: list[dict[str, Any]] | None,
        attributes: dict[str, Any],
        response: dict[str, Any] | None = None,
        output_messages: list[dict[str, Any]] | None = None,
        tool_names: list[str] | None = None,
    ) -> None:
        prompt_tokens, completion_tokens, total_tokens = _usage_counts(usage)
        attributes[GEN_AI_USAGE_INPUT_TOKENS] = prompt_tokens
        attributes[GEN_AI_USAGE_OUTPUT_TOKENS] = completion_tokens
        attributes[GARDEN_MODEL_TOTAL_TOKENS] = total_tokens
        attributes[GEN_AI_RESPONSE_STATUS] = {
            "ok": "completed",
            "upstream_error": "failed",
            "cancelled": "cancelled",
        }[status]
        response_model = response.get("model") if response is not None else None
        attributes[GEN_AI_RESPONSE_MODEL] = (
            response_model if isinstance(response_model, str) else model
        )
        if response is not None:
            response_id = response.get("id")
            if isinstance(response_id, str) and response_id:
                attributes[GEN_AI_RESPONSE_ID] = response_id
            output_messages = _response_messages(response)
            choices = response.get("choices")
            if isinstance(choices, list):
                messages: list[dict[str, Any]] = []
                for raw_choice in cast("list[object]", choices):
                    if not isinstance(raw_choice, dict):
                        continue
                    choice = cast("dict[str, Any]", raw_choice)
                    message = choice.get("message")
                    if isinstance(message, dict):
                        messages.append(cast("dict[str, Any]", message))
                tool_names = _tool_names(messages)
        if tool_names:
            attributes[GARDEN_MODEL_TOOL_NAMES] = tool_names
        capture_on_span = self._settings.gen_ai_capture_content in GEN_AI_CAPTURE_SPAN_MODES
        capture_on_event = self._settings.gen_ai_capture_content in GEN_AI_CAPTURE_EVENT_MODES
        if capture_content and capture_on_span and output_messages:
            attributes[GEN_AI_OUTPUT_MESSAGES] = json.dumps(output_messages, ensure_ascii=False)
        if status == "ok":
            span.set_status(Status(StatusCode.OK))
        else:
            attributes["error.type"] = status
            span.set_status(Status(StatusCode.ERROR, status))
        for name, value in attributes.items():
            span.set_attribute(name, value)
        if capture_content and capture_on_event:
            event_attributes = dict(attributes)
            if input_messages is not None:
                event_attributes[GEN_AI_INPUT_MESSAGES] = input_messages
            if output_messages:
                event_attributes[GEN_AI_OUTPUT_MESSAGES] = output_messages
            _logs.get_logger("garden.model_gateway").emit(
                event_name=GEN_AI_INFERENCE_EVENT,
                severity_number=SeverityNumber.DEBUG,
                attributes=event_attributes,
                context=trace.set_span_in_context(span),
            )
        span.end()

    async def complete(
        self,
        *,
        person_id: uuid.UUID,
        cli_login_id: uuid.UUID | None,
        body: dict[str, Any],
        agent_conversation_id: str | None = None,
        model_side_call_type: str | None = None,
    ) -> dict[str, Any]:
        if not self._settings.agent_model_upstream_url:
            raise ModelUnavailable("The model gateway has no upstream model configured.")
        model, now = await self._admit(person_id=person_id, cli_login_id=cli_login_id, body=body)
        span, capture_content, input_messages, trace_attributes = self._start_chat_span(
            person_id,
            cli_login_id,
            model,
            now,
            body,
            agent_conversation_id,
            model_side_call_type,
        )
        try:
            response = await self._forward(body)
        except UpstreamFailed:
            usage: dict[str, Any] = {}
            try:
                self._finish_chat_span(
                    span,
                    "upstream_error",
                    usage,
                    model,
                    capture_content=capture_content,
                    input_messages=input_messages,
                    attributes=trace_attributes,
                )
            finally:
                await self._settle(person_id, cli_login_id, model, now, "upstream_error", usage)
            raise
        except BaseException:
            # The caller went away while the upstream was thinking. The call still counts.
            usage = {}
            try:
                self._finish_chat_span(
                    span,
                    "cancelled",
                    usage,
                    model,
                    capture_content=capture_content,
                    input_messages=input_messages,
                    attributes=trace_attributes,
                )
            finally:
                await self._settle(person_id, cli_login_id, model, now, "cancelled", usage)
            raise
        reported: object = response.get("usage")
        usage = cast("dict[str, Any]", reported) if isinstance(reported, dict) else {}
        try:
            self._finish_chat_span(
                span,
                "ok",
                usage,
                model,
                capture_content=capture_content,
                input_messages=input_messages,
                attributes=trace_attributes,
                response=response,
            )
        finally:
            await self._settle(person_id, cli_login_id, model, now, "ok", usage)
        return response

    async def stream(
        self,
        *,
        person_id: uuid.UUID,
        cli_login_id: uuid.UUID | None,
        body: dict[str, Any],
        agent_conversation_id: str | None = None,
        model_side_call_type: str | None = None,
    ) -> AsyncIterator[bytes]:
        """Open a streamed completion. Refusals and upstream failures raise before any bytes
        go out. The usage is asked for in the stream, so it can be recorded when the answer ends."""
        model, now = await self._admit(person_id=person_id, cli_login_id=cli_login_id, body=body)
        span, capture_content, input_messages, trace_attributes = self._start_chat_span(
            person_id,
            cli_login_id,
            model,
            now,
            body,
            agent_conversation_id,
            model_side_call_type,
        )
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
            usage: dict[str, Any] = {}
            try:
                self._finish_chat_span(
                    span,
                    "upstream_error",
                    usage,
                    model,
                    capture_content=capture_content,
                    input_messages=input_messages,
                    attributes=trace_attributes,
                )
            finally:
                await self._settle(person_id, cli_login_id, model, now, "upstream_error", usage)
            raise UpstreamFailed("The upstream model could not answer.") from error
        except BaseException:
            usage = {}
            with anyio.CancelScope(shield=True):
                await client.aclose()
                self._finish_chat_span(
                    span,
                    "cancelled",
                    usage,
                    model,
                    capture_content=capture_content,
                    input_messages=input_messages,
                    attributes=trace_attributes,
                )
                await self._settle(person_id, cli_login_id, model, now, "cancelled", usage)
            raise
        if upstream.status_code >= 400:
            await upstream.aclose()
            await client.aclose()
            usage = {}
            try:
                self._finish_chat_span(
                    span,
                    "upstream_error",
                    usage,
                    model,
                    capture_content=capture_content,
                    input_messages=input_messages,
                    attributes=trace_attributes,
                )
            finally:
                await self._settle(person_id, cli_login_id, model, now, "upstream_error", usage)
            raise UpstreamFailed("The upstream model could not answer.")
        return self._relay(
            client,
            upstream,
            person_id,
            cli_login_id,
            model,
            now,
            span,
            capture_content,
            input_messages,
            trace_attributes,
        )

    async def _relay(
        self,
        client: httpx.AsyncClient,
        upstream: httpx.Response,
        person_id: uuid.UUID,
        cli_login_id: uuid.UUID | None,
        model: str,
        now: datetime,
        span: Span,
        capture_content: bool,
        input_messages: list[dict[str, Any]] | None,
        trace_attributes: dict[str, Any],
    ) -> AsyncIterator[bytes]:
        """Pass the upstream's server-sent events through unchanged and count usage when it ends."""
        usage: dict[str, Any] = {}
        status = "ok"
        content: list[str] = []
        tool_names: list[str] = []
        lines = upstream.aiter_lines()
        try:
            async for line in lines:
                if line.startswith("data:") and '"usage"' in line:
                    _merge(usage, _event_usage(line))
                text, names = _stream_delta(line)
                if capture_content and text:
                    content.append(text)
                for name in names:
                    if name not in tool_names:
                        tool_names.append(name)
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
            output_messages = (
                [{"role": "assistant", "parts": [{"type": "text", "content": "".join(content)}]}]
                if content
                else []
            )
            with anyio.CancelScope(shield=True):
                await upstream.aclose()
                await client.aclose()
                try:
                    self._finish_chat_span(
                        span,
                        status,
                        usage,
                        model,
                        capture_content=capture_content,
                        input_messages=input_messages,
                        attributes=trace_attributes,
                        output_messages=output_messages,
                        tool_names=tool_names,
                    )
                finally:
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
        prompt, completion, total = _usage_counts(usage)
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
