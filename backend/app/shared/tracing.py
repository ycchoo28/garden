"""OpenTelemetry spans for API operations, with Garden's shared trace attributes."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import cast

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
    GEN_AI_OPERATION_NAME,
)
from opentelemetry import propagate, trace
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class TraceAPIOperations:
    """Trace each API request and continue any incoming W3C trace context."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        scope.setdefault("state", {})
        carrier = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])
        }
        status_code: int | None = None

        async def traced_send(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                code = message.get("status")
                if isinstance(code, int):
                    status_code = code
                self._add_agent_identity_headers(scope, message)
            await send(message)

        with trace.get_tracer("garden.api").start_as_current_span(
            "http.server.request",
            context=propagate.extract(carrier),
            kind=SpanKind.SERVER,
        ) as span:
            self._base_attributes(span, scope)
            try:
                await self._app(scope, receive, traced_send)
            except Exception as error:
                span.record_exception(error)
                span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                self._finish_attributes(span, scope, status_code)

    @staticmethod
    def _base_attributes(span: Span, scope: Scope) -> None:
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
        side_call = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.decode("latin-1").lower() == GARDEN_EVAL_SIDE_CALL_HEADER
            ),
            None,
        )
        if side_call:
            span.set_attribute(GARDEN_EVAL_SIDE_CALL, side_call)

    @staticmethod
    def _finish_attributes(span: Span, scope: Scope, status_code: int | None) -> None:
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
        if agent:
            span.set_attribute(GEN_AI_OPERATION_NAME, "execute_tool")

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
