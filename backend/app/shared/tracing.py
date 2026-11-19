"""OpenTelemetry spans for API operations, with Garden's shared trace attributes."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import cast
from urllib.parse import parse_qs

from garden.trace_contract import (
    GARDEN_AGENT_FLAG,
    GARDEN_AGENT_LOGIN_ID,
    GARDEN_AGENT_LOGIN_ID_HEADER,
    GARDEN_CONTENT_CAPTURED,
    GARDEN_EVAL_RUN_ID,
    GARDEN_EVAL_RUN_ID_ENV,
    GARDEN_EVAL_SIDE_CALL,
    GARDEN_EVAL_SIDE_CALL_HEADER,
    GARDEN_EVAL_TRIAL_ID,
    GARDEN_EVAL_TRIAL_ID_ENV,
    GARDEN_PERSON_ID,
    GARDEN_PERSON_ID_HEADER,
    GEN_AI_CAPTURE_CONTENT_ENV,
    GEN_AI_CAPTURE_SPAN_MODES,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_NAME,
)
from opentelemetry import propagate, trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_MAX_CAPTURED_BYTES = 32_768
_SENSITIVE_KEYS = frozenset(
    {"authorization", "token", "secret", "password", "api_key", "code_verifier"}
)
_MULTIPART_DISPOSITION_NAME = re.compile(rb'filename="([^"]+)"')
_MULTIPART_CONTENT_TYPE = re.compile(rb"Content-Type:\s*([^\r\n]+)", re.IGNORECASE)


def _capture_span_content() -> bool:
    environment = os.environ.get("GARDEN_ENVIRONMENT", "dev").strip().lower()
    mode = os.environ.get(GEN_AI_CAPTURE_CONTENT_ENV, "NO_CONTENT").strip().upper()
    return environment == "dev" and mode in GEN_AI_CAPTURE_SPAN_MODES


def _header_value(headers: list[tuple[bytes, bytes]], target: str) -> str | None:
    lowered = target.lower()
    for name, value in headers:
        if name.decode("latin-1").lower() == lowered:
            return value.decode("latin-1")
    return None


def _redact_sensitive(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): (
                "[REDACTED]" if str(key).lower() in _SENSITIVE_KEYS else _redact_sensitive(v)
            )
            for key, v in cast(dict[object, object], value).items()
        }
    if isinstance(value, list):
        return [_redact_sensitive(item) for item in cast(list[object], value)]
    return value


def _truncate_json(value: object) -> str:
    encoded = json.dumps(_redact_sensitive(value), ensure_ascii=False, default=str)
    if len(encoded) <= _MAX_CAPTURED_BYTES:
        return encoded
    return json.dumps(
        {"truncated": True, "excerpt": encoded[: _MAX_CAPTURED_BYTES - 128]},
        ensure_ascii=False,
    )


def _decode_request_body(body: bytes, content_type: str | None) -> tuple[str, object] | None:
    if not body:
        return None
    lowered = (content_type or "").lower()
    if "multipart/form-data" in lowered:
        head = body[:2048]
        name_match = _MULTIPART_DISPOSITION_NAME.search(head)
        type_match = _MULTIPART_CONTENT_TYPE.search(head)
        summary: dict[str, object] = {"multipart": True, "bytes": len(body)}
        if name_match is not None:
            summary["name"] = name_match[1].decode("utf-8", errors="replace")
        if type_match is not None:
            summary["content_type"] = type_match[1].decode("utf-8", errors="replace").strip()
        return "multipart", summary
    if "json" in lowered or not lowered:
        try:
            return "body", json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return None
    return "body", {"content_type": content_type, "bytes": len(body)}


def _is_tool_route(route: object) -> bool:
    extra = getattr(route, "openapi_extra", None)
    if not isinstance(extra, dict):
        return True
    agent_meta = cast(dict[str, object], extra).get("x-agent")
    return not (
        isinstance(agent_meta, dict) and cast(dict[str, object], agent_meta).get("tool") is False
    )


def _should_capture_payload(path: str) -> bool:
    stripped = path.rstrip("/")
    return not (
        stripped.startswith("/api/agent/model")
        or stripped in {"/api/agent/cli/token", "/api/agent/mcp"}
        or stripped.startswith("/auth")
    )


class TraceAPIOperations:
    """Trace each API request and continue any incoming W3C trace context."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        scope.setdefault("state", {})
        headers = cast(list[tuple[bytes, bytes]], scope.get("headers", []))
        carrier = {
            name.decode("latin-1").lower(): value.decode("latin-1") for name, value in headers
        }
        path = str(scope.get("path", ""))
        method = str(scope.get("method", ""))
        capture_payload = _capture_span_content() and _should_capture_payload(path)
        request_parts = bytearray()
        request_total_bytes = 0
        response_parts = bytearray()
        response_total_bytes = 0
        response_content_type: str | None = None
        status_code: int | None = None

        async def traced_receive() -> Message:
            nonlocal request_total_bytes
            message = await receive()
            if (
                capture_payload
                and method in {"POST", "PUT", "PATCH", "DELETE"}
                and message.get("type") == "http.request"
            ):
                payload = message.get("body", b"")
                if isinstance(payload, (bytes, bytearray)) and payload:
                    request_total_bytes += len(payload)
                    remaining = _MAX_CAPTURED_BYTES - len(request_parts)
                    if remaining > 0:
                        request_parts.extend(payload[:remaining])
            return message

        async def traced_send(message: Message) -> None:
            nonlocal status_code, response_content_type, response_total_bytes
            msg_type = message["type"]
            if msg_type == "http.response.start":
                code = message.get("status")
                if isinstance(code, int):
                    status_code = code
                raw_headers = cast(list[tuple[bytes, bytes]], message.get("headers", []))
                response_content_type = _header_value(raw_headers, "content-type")
                self._add_agent_identity_headers(scope, message)
            elif (
                msg_type == "http.response.body"
                and capture_payload
                and response_content_type is not None
                and "json" in response_content_type.lower()
            ):
                payload = message.get("body", b"")
                if isinstance(payload, (bytes, bytearray)) and payload:
                    response_total_bytes += len(payload)
                    remaining = _MAX_CAPTURED_BYTES - len(response_parts)
                    if remaining > 0:
                        response_parts.extend(payload[:remaining])
            await send(message)

        with trace.get_tracer("garden.api").start_as_current_span(
            "http.server.request",
            context=propagate.extract(carrier),
            kind=SpanKind.SERVER,
        ) as span:
            side_call = self._base_attributes(span, scope)
            try:
                await self._app(
                    scope,
                    traced_receive if capture_payload else receive,
                    traced_send,
                )
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                self._finish_attributes(
                    span,
                    scope,
                    status_code,
                    side_call=side_call,
                    capture_payload=capture_payload,
                    request_content_type=_header_value(headers, "content-type"),
                    request_body=bytes(request_parts),
                    request_total_bytes=request_total_bytes,
                    response_content_type=response_content_type,
                    response_body=bytes(response_parts),
                    response_total_bytes=response_total_bytes,
                )

    @staticmethod
    def _base_attributes(span: Span, scope: Scope) -> str | None:
        span.set_attribute("http.request.method", str(scope.get("method", "")))
        span.set_attribute(GARDEN_CONTENT_CAPTURED, False)
        span.set_attribute(GARDEN_AGENT_FLAG, False)
        for attribute, environ in (
            (GARDEN_EVAL_RUN_ID, GARDEN_EVAL_RUN_ID_ENV),
            (GARDEN_EVAL_TRIAL_ID, GARDEN_EVAL_TRIAL_ID_ENV),
        ):
            value = os.environ.get(environ)
            if value:
                span.set_attribute(attribute, value)
        side_call = _header_value(
            cast(list[tuple[bytes, bytes]], scope.get("headers", [])),
            GARDEN_EVAL_SIDE_CALL_HEADER,
        )
        if side_call:
            span.set_attribute(GARDEN_EVAL_SIDE_CALL, side_call)
        return side_call

    @staticmethod
    def _finish_attributes(
        span: Span,
        scope: Scope,
        status_code: int | None,
        *,
        side_call: str | None = None,
        capture_payload: bool = False,
        request_content_type: str | None = None,
        request_body: bytes = b"",
        request_total_bytes: int = 0,
        response_content_type: str | None = None,
        response_body: bytes = b"",
        response_total_bytes: int = 0,
    ) -> None:
        path = str(scope.get("path", ""))
        route = scope.get("route")
        operation_id = getattr(route, "operation_id", None)
        if isinstance(operation_id, str) and operation_id:
            span.update_name(operation_id)
        elif path.rstrip("/") == "/api/agent/mcp":
            span.update_name("agent.mcp")
        else:
            span.update_name(f"{scope.get('method', '')} {path}")

        route_path = getattr(route, "path_format", None)
        if isinstance(route_path, str):
            span.set_attribute("http.route", route_path)
        if status_code is not None:
            span.set_attribute("http.response.status_code", status_code)
            if status_code >= 500:
                span.set_status(Status(StatusCode.ERROR))

        state = cast(Mapping[str, object], scope["state"])
        person_id = state.get(GARDEN_PERSON_ID)
        login_id = state.get(GARDEN_AGENT_LOGIN_ID)
        agent = state.get(GARDEN_AGENT_FLAG) is True
        if isinstance(person_id, str):
            span.set_attribute(GARDEN_PERSON_ID, person_id)
        if isinstance(login_id, str):
            span.set_attribute(GARDEN_AGENT_LOGIN_ID, login_id)
        span.set_attribute(GARDEN_AGENT_FLAG, agent)
        is_execute_tool = (
            agent
            and not side_call
            and _is_tool_route(route)
            and path.rstrip("/") != "/api/agent/mcp"
        )
        if is_execute_tool:
            span.set_attribute(GEN_AI_OPERATION_NAME, "execute_tool")
            if isinstance(operation_id, str) and operation_id:
                span.set_attribute(GEN_AI_TOOL_NAME, operation_id)
            if capture_payload:
                _attach_tool_payload(
                    span,
                    scope,
                    status_code,
                    request_content_type=request_content_type,
                    request_body=request_body,
                    request_total_bytes=request_total_bytes,
                    response_content_type=response_content_type,
                    response_body=response_body,
                    response_total_bytes=response_total_bytes,
                )

    @staticmethod
    def _add_agent_identity_headers(scope: Scope, message: Message) -> None:
        state = cast(Mapping[str, object], scope["state"])
        if state.get(GARDEN_AGENT_FLAG) is not True:
            return
        person_id = state.get(GARDEN_PERSON_ID)
        login_id = state.get(GARDEN_AGENT_LOGIN_ID)
        headers = list(message.get("headers", []))
        if isinstance(person_id, str):
            headers.append((GARDEN_PERSON_ID_HEADER.encode("ascii"), person_id.encode("ascii")))
        if isinstance(login_id, str):
            headers.append((GARDEN_AGENT_LOGIN_ID_HEADER.encode("ascii"), login_id.encode("ascii")))
        message["headers"] = headers


def _attach_tool_payload(
    span: Span,
    scope: Scope,
    status_code: int | None,
    *,
    request_content_type: str | None,
    request_body: bytes,
    request_total_bytes: int,
    response_content_type: str | None,
    response_body: bytes,
    response_total_bytes: int,
) -> None:
    arguments: dict[str, object] = {}
    raw_path_params = scope.get("path_params")
    if isinstance(raw_path_params, Mapping):
        for key, value in cast(Mapping[object, object], raw_path_params).items():
            arguments[str(key)] = (
                str(value) if not isinstance(value, (str, int, float, bool)) else value
            )
    raw_query = scope.get("query_string")
    if isinstance(raw_query, bytes) and raw_query:
        for key, values in parse_qs(raw_query.decode("utf-8", errors="replace")).items():
            arguments[key] = values[0] if len(values) == 1 else values
    if (
        request_total_bytes > len(request_body)
        and "multipart/form-data" not in (request_content_type or "").lower()
    ):
        arguments["body"] = {"truncated": True, "bytes": request_total_bytes}
    else:
        decoded_req = _decode_request_body(request_body, request_content_type)
        if decoded_req is not None:
            field_name, payload = decoded_req
            if field_name == "multipart" and isinstance(payload, dict):
                cast(dict[str, object], payload)["bytes"] = request_total_bytes
            arguments[field_name] = payload

    result: object
    if response_total_bytes > len(response_body):
        result = {"truncated": True, "bytes": response_total_bytes, "status_code": status_code}
    elif response_body and response_content_type and "json" in response_content_type.lower():
        try:
            result = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            result = {"status_code": status_code, "bytes": response_total_bytes}
    elif status_code is not None:
        result = {"status_code": status_code}
    else:
        result = {}

    args_json = _truncate_json(arguments)
    result_json = _truncate_json(result)
    span.set_attribute(GARDEN_CONTENT_CAPTURED, True)
    span.set_attribute(GEN_AI_TOOL_CALL_ARGUMENTS, args_json)
    span.set_attribute(GEN_AI_TOOL_CALL_RESULT, result_json)
    span.set_attribute(GEN_AI_INPUT_MESSAGES, args_json)
    span.set_attribute(GEN_AI_OUTPUT_MESSAGES, result_json)
