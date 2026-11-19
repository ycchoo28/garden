"""`garden mcp`: a stdio MCP server over the same engine as the commands.

Messages are newline-delimited JSON-RPC 2.0. Stdout carries only protocol messages; diagnostics go
to stderr.
Annotations come from the tool: reads say readOnlyHint, ordinary saves say destructiveHint false,
and critical routes say destructiveHint true. A critical call also waits for the person's approval
in the browser, and its link is sent as a log notification meanwhile (ADR 0012).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from typing import Any, TextIO

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from garden import __version__
from garden.engine import Engine, Notify, Tool
from garden.shared import GardenError, as_object, is_object
from garden.trace_contract import (
    GARDEN_AGENT_FLAG,
    GARDEN_CONTENT_CAPTURED,
    GARDEN_EVAL_RUN_ID,
    GARDEN_EVAL_RUN_ID_ENV,
    GARDEN_EVAL_TRIAL_ID,
    GARDEN_EVAL_TRIAL_ID_ENV,
    GEN_AI_CAPTURE_CONTENT_ENV,
    GEN_AI_CAPTURE_SPAN_MODES,
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_OPERATION_NAME,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_TOOL_CALL_ARGUMENTS,
    GEN_AI_TOOL_CALL_RESULT,
    GEN_AI_TOOL_NAME,
)

PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26")
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602


def serve(engine: Engine, stdin: TextIO, stdout: TextIO, stderr: TextIO | None = None) -> int:
    notify = _notifier(stdout, stderr or sys.stderr)
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _send(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": PARSE_ERROR, "message": "Not JSON."},
                },
            )
            continue
        if not is_object(message):
            # A JSON value that is not a message object, such as an array or a number, must not
            # stop the server: it gets an invalid-request reply, and the next line is read.
            _send(
                stdout,
                {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": INVALID_REQUEST, "message": "Not a JSON-RPC message."},
                },
            )
            continue
        reply = handle(engine, message, notify=notify)
        if reply is not None:
            _send(stdout, reply)
    return 0


def serve_stdio(engine: Engine) -> int:
    return serve(engine, sys.stdin, sys.stdout)


def _notifier(stdout: TextIO, stderr: TextIO) -> Notify:
    """Tells the agent's client and the person at the terminal what a tool call is waiting for.

    A tool call waits for a critical action's approval, and the link is the only thing the person
    needs to see. The client gets it as a log notification while the call is still open, and the
    person sees it on stderr.
    """

    def notify(text: str) -> None:
        _send(
            stdout,
            {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "notice", "logger": "garden", "data": text},
            },
        )
        print(text, file=stderr, flush=True)

    return notify


def handle(
    engine: Engine,
    message: dict[str, Any],
    notify: Notify | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any] | None:
    """One message in, one reply out. Notifications have no id and get no reply."""
    method = message.get("method")
    request_id = message.get("id")
    if request_id is None:
        return None
    raw_params: Any = message.get("params") or {}
    if not is_object(raw_params):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": INVALID_PARAMS, "message": "Params must be an object."},
        }
    try:
        result = _dispatch(engine, str(method), raw_params, notify, cancel_event)
    except _ProtocolError as error:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": error.code, "message": error.message},
        }
    except GardenError as error:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32000, "message": error.title, "data": error.to_dict()["error"]},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class _ProtocolError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _dispatch(
    engine: Engine,
    method: str,
    params: dict[str, Any],
    notify: Notify | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    if method == "initialize":
        requested = str(params.get("protocolVersion", PROTOCOL_VERSION))
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        return {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}, "logging": {}},
            "serverInfo": {"name": "garden", "version": __version__},
            "instructions": "Tools come from the Garden portal, limited to what the person may do.",
        }
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [_describe(tool, hosted_files=engine.hosted_files) for tool in engine.tools()]
        }
    if method == "tools/call":
        return _call(engine, params, notify, cancel_event)
    raise _ProtocolError(METHOD_NOT_FOUND, f"Unknown method {method}.")


def _describe(tool: Tool, *, hosted_files: bool = False) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.summary,
        "inputSchema": tool.input_schema(hosted_files=hosted_files),
        "annotations": {"title": tool.op_id, **tool.hints()},
    }


def _call(
    engine: Engine,
    params: dict[str, Any],
    notify: Notify | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str):
        raise _ProtocolError(INVALID_PARAMS, "tools/call needs a tool name.")
    raw_arguments = params.get("arguments")
    if raw_arguments is not None and not is_object(raw_arguments):
        raise _ProtocolError(INVALID_PARAMS, "Arguments must be an object.")
    arguments = as_object(raw_arguments)
    capture_content = (
        os.environ.get("GARDEN_ENVIRONMENT", "dev").strip().lower() == "dev"
        and os.environ.get(GEN_AI_CAPTURE_CONTENT_ENV, "NO_CONTENT").strip().upper()
        in GEN_AI_CAPTURE_SPAN_MODES
    )
    attributes: dict[str, str | bool] = {
        GEN_AI_OPERATION_NAME: "execute_tool",
        GEN_AI_TOOL_NAME: name,
        GARDEN_AGENT_FLAG: True,
        GARDEN_CONTENT_CAPTURED: capture_content,
    }
    if capture_content:
        args_json = _bounded_json(arguments)
        attributes[GEN_AI_TOOL_CALL_ARGUMENTS] = args_json
        attributes[GEN_AI_INPUT_MESSAGES] = args_json
    for attribute, environ in (
        (GARDEN_EVAL_RUN_ID, GARDEN_EVAL_RUN_ID_ENV),
        (GARDEN_EVAL_TRIAL_ID, GARDEN_EVAL_TRIAL_ID_ENV),
    ):
        value = os.environ.get(environ)
        if value:
            attributes[attribute] = value

    with trace.get_tracer("garden.mcp").start_as_current_span(name, attributes=attributes) as span:
        tool = engine.tool(name)
        if tool is None:
            error_body = {
                "title": "Tool not available",
                "detail": f"{name} is not a tool you may use.",
                "fix": "Call tools/list to see the tools available to you.",
            }
            if capture_content:
                _set_result_attributes(span, {"error": error_body})
            return _tool_error(error_body)
        try:
            value = engine.call(tool, arguments, notify=notify, cancel_event=cancel_event)
        except GardenError as error:
            span.set_status(Status(StatusCode.ERROR))
            error_body = error.to_dict()["error"]
            if capture_content:
                _set_result_attributes(span, {"error": error_body})
            return _tool_error(error_body)
        if capture_content:
            _set_result_attributes(span, value)
        text = json.dumps(value, ensure_ascii=False)
        result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
        if isinstance(value, dict):
            result["structuredContent"] = value
        return result


_MAX_TRACE_JSON_BYTES = 32_768


def _bounded_json(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if len(encoded) <= _MAX_TRACE_JSON_BYTES:
        return encoded
    return json.dumps(
        {"truncated": True, "preview": encoded[: _MAX_TRACE_JSON_BYTES - 128]},
        ensure_ascii=False,
    )


def _set_result_attributes(span: trace.Span, value: object) -> None:
    encoded = _bounded_json(value)
    span.set_attribute(GEN_AI_TOOL_CALL_RESULT, encoded)
    span.set_attribute(GEN_AI_OUTPUT_MESSAGES, encoded)


def _tool_error(body: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps({"error": body}, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _send(stdout: TextIO, message: dict[str, Any]) -> None:
    stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    stdout.flush()
