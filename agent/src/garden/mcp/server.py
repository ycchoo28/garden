"""`garden mcp`: a stdio MCP server over the same engine as the commands.

Messages are newline-delimited JSON-RPC 2.0. Stdout carries only protocol messages; diagnostics go
to stderr.
Annotations come from the tool: reads say readOnlyHint, ordinary saves say destructiveHint false,
and critical routes say destructiveHint true. A critical call also waits for the person's approval
in the browser, and its link is sent as a log notification meanwhile (ADR 0012).
"""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from garden import __version__
from garden.engine import Engine, Notify, Tool
from garden.shared import GardenError, as_object, is_object

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
    engine: Engine, message: dict[str, Any], notify: Notify | None = None
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
        result = _dispatch(engine, str(method), raw_params, notify)
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
    engine: Engine, method: str, params: dict[str, Any], notify: Notify | None
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
        return {"tools": [_describe(tool) for tool in engine.tools()]}
    if method == "tools/call":
        return _call(engine, params, notify)
    raise _ProtocolError(METHOD_NOT_FOUND, f"Unknown method {method}.")


def _describe(tool: Tool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.summary,
        "inputSchema": tool.input_schema(),
        "annotations": {"title": tool.op_id, **tool.hints()},
    }


def _call(engine: Engine, params: dict[str, Any], notify: Notify | None) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str):
        raise _ProtocolError(INVALID_PARAMS, "tools/call needs a tool name.")
    raw_arguments = params.get("arguments")
    if raw_arguments is not None and not is_object(raw_arguments):
        raise _ProtocolError(INVALID_PARAMS, "Arguments must be an object.")
    arguments = as_object(raw_arguments)
    tool = engine.tool(name)
    if tool is None:
        return _tool_error(
            {
                "title": "Tool not available",
                "detail": f"{name} is not a tool you may use.",
                "fix": "Call tools/list to see the tools available to you.",
            }
        )
    try:
        value = engine.call(tool, arguments, notify=notify)
    except GardenError as error:
        return _tool_error(error.to_dict()["error"])
    text = json.dumps(value, ensure_ascii=False)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": False}
    if isinstance(value, dict):
        result["structuredContent"] = value
    return result


def _tool_error(body: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps({"error": body}, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _send(stdout: TextIO, message: dict[str, Any]) -> None:
    stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    stdout.flush()
