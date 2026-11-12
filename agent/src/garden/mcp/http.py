"""Stateless MCP Streamable HTTP transport for the hosted portal endpoint."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from garden.engine import ContractCache, Engine, PortalClient
from garden.mcp.server import handle
from garden.shared import GardenError, as_object, is_object

THREAD_POOL_SIZE = 40
_BEARER = re.compile(r"ag_[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class HostedMCPSettings:
    enabled: bool = True
    # The address the Engine calls. None means the loopback address of the request's own port.
    api_base_url: str | None = None
    allowed_origins: frozenset[str] = frozenset()
    files_max_bytes: int = 5 * 1024 * 1024
    pending_per_login: int = 3
    pending_total: int = 20

    def __post_init__(self) -> None:
        if self.files_max_bytes < 1:
            raise ValueError("files_max_bytes must be positive")
        if self.pending_per_login < 1 or self.pending_total < 1:
            raise ValueError("confirmation wait limits must be positive")
        if self.pending_per_login > self.pending_total:
            raise ValueError("pending_per_login cannot exceed pending_total")
        if self.pending_total >= THREAD_POOL_SIZE:
            raise ValueError("pending_total must stay below the MCP worker thread pool size")


def create_streamable_http_app(
    settings_provider: Callable[[], HostedMCPSettings],
) -> Callable[..., Any]:
    """Build the ASGI endpoint. The backend supplies settings without becoming MCP-aware."""
    return _StreamableHTTPApp(settings_provider)


class _StreamableHTTPApp:
    def __init__(self, settings_provider: Callable[[], HostedMCPSettings]) -> None:
        self._settings_provider = settings_provider
        self._executor = ThreadPoolExecutor(
            max_workers=THREAD_POOL_SIZE, thread_name_prefix="garden-mcp"
        )
        self._contract_cache: ContractCache = {}
        self._contract_cache_lock = threading.Lock()
        self._pending_lock = asyncio.Lock()
        self._pending_by_login: dict[str, int] = {}
        self._pending_total = 0

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            return
        settings = self._settings_provider()
        if not settings.enabled:
            await _problem(send, 404, "Not found", "The hosted MCP endpoint is turned off.")
            return
        if scope.get("path", "/") not in ("", "/", "/api/agent/mcp"):
            await _problem(send, 404, "Not found", "There is no MCP endpoint at this path.")
            return

        headers: dict[bytes, bytes] = {}
        for key, value in scope.get("headers", []):
            normalized = key.lower()
            if normalized in headers and normalized in {
                b"authorization",
                b"origin",
                b"content-length",
                b"content-type",
                b"access-control-request-method",
                b"access-control-request-headers",
            }:
                await _problem(send, 400, "Duplicate header", "A header was sent more than once.")
                return
            headers[normalized] = value
        origin = headers.get(b"origin")
        if origin is not None and origin.decode("latin-1") not in settings.allowed_origins:
            await _problem(send, 403, "Origin not allowed", "This browser origin may not call MCP.")
            return
        if origin is not None:
            send = _cors_sender(send, origin.decode("latin-1"))
        method = scope["method"].upper()
        if method == "OPTIONS":
            await _preflight(send, headers, origin is not None)
            return
        if method != "POST":
            await _problem(
                send,
                405,
                "Method not allowed",
                "Use POST. The endpoint has no server-initiated stream.",
                [(b"allow", b"POST, OPTIONS")],
            )
            return
        token = _bearer_token(headers.get(b"authorization", b""))
        if token is None:
            await _unauthorized(send)
            return

        content_type = headers.get(b"content-type", b"").decode("latin-1")
        if content_type.split(";", 1)[0].strip().lower() != "application/json":
            await _problem(
                send, 415, "Unsupported media type", "Send Content-Type: application/json."
            )
            return
        if not _accepts_streamable_http(headers.get(b"accept", b"")):
            await _problem(
                send,
                406,
                "Not acceptable",
                "Accept application/json and text/event-stream.",
            )
            return

        api_base_url = settings.api_base_url or _loopback_api_url(scope)
        if api_base_url is None:
            await _problem(
                send,
                503,
                "API address unknown",
                "The server did not report its port. Set the hosted MCP API base URL.",
            )
            return
        engine = Engine(
            PortalClient(api_base_url, token=token),
            cache_dir=None,
            hosted_files=True,
            max_file_bytes=settings.files_max_bytes,
            contract_cache=self._contract_cache,
            contract_cache_lock=self._contract_cache_lock,
            snapshot_permissions=True,
        )
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, engine.permissions)
        except (GardenError, OSError) as error:
            if isinstance(error, GardenError) and error.status == 403:
                await _problem(send, 403, "Forbidden", "This CLI login may not use MCP.")
                return
            await _portal_failure(send, None, error)
            return

        max_body_bytes = (settings.files_max_bytes * 14 + 9) // 10 + 16_384
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                size = int(content_length)
                if size < 0:
                    await _problem(send, 400, "Invalid Content-Length", "The length is negative.")
                    return
                if size > max_body_bytes:
                    await _too_large(send, settings.files_max_bytes)
                    return
            except ValueError:
                await _problem(send, 400, "Invalid Content-Length", "The length is not a number.")
                return
        body, oversized = await _read_body(receive, max_body_bytes)
        if body is None:
            return
        if oversized:
            await _too_large(send, settings.files_max_bytes)
            return

        message, parse_error = _parse_message(body)
        if parse_error is not None:
            await _json_response(send, 400, parse_error)
            return
        assert message is not None
        if "id" not in message:
            await _respond(send, 202, b"", [])
            return

        protocol_version = headers.get(b"mcp-protocol-version")
        if protocol_version is not None and protocol_version.decode("latin-1") not in {
            "2025-06-18",
            "2025-03-26",
        }:
            await _json_response(
                send,
                400,
                _rpc_error(message["id"], -32600, "Unsupported MCP-Protocol-Version."),
            )
            return

        pending_key: str | None = None
        if message.get("method") == "tools/call":
            arguments = as_object(message.get("params"))
            tool_name = arguments.get("name")
            if isinstance(tool_name, str):
                try:
                    tool = await loop.run_in_executor(self._executor, engine.tool, tool_name)
                except (GardenError, OSError) as error:
                    await _portal_failure(send, message["id"], error)
                    return
                if tool is not None and tool.critical:
                    pending_key = hashlib.sha256(token.encode("utf-8")).hexdigest()
                    if not await self._reserve(pending_key, settings):
                        await _json_response(
                            send,
                            200,
                            _rpc_error(
                                message["id"],
                                -32000,
                                "Too many confirmations are pending. Try again when one finishes.",
                            ),
                        )
                        return

        cancel_event = threading.Event()
        notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

        def notify(text: str) -> None:
            notification = {
                "jsonrpc": "2.0",
                "method": "notifications/message",
                "params": {"level": "notice", "logger": "garden", "data": text},
            }
            loop.call_soon_threadsafe(notifications.put_nowait, notification)

        def process() -> dict[str, Any] | None:
            try:
                return handle(engine, message, notify=notify, cancel_event=cancel_event)
            except Exception:
                return _rpc_error(message.get("id"), -32603, "Internal error.")

        worker = loop.run_in_executor(self._executor, process)
        disconnect = asyncio.create_task(_watch_disconnect(receive, cancel_event))
        first_notification = asyncio.create_task(notifications.get())
        next_notification: asyncio.Task[dict[str, Any]] | None = None
        try:
            finished, _ = await asyncio.wait(
                {worker, disconnect, first_notification},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect in finished:
                await worker
                return
            if first_notification not in finished:
                await _json_response(send, 200, await worker)
                return

            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [
                        (b"content-type", b"text/event-stream"),
                        (b"cache-control", b"no-cache"),
                        (b"x-accel-buffering", b"no"),
                    ],
                }
            )
            await _send_sse(send, first_notification.result())
            while True:
                next_notification = asyncio.create_task(notifications.get())
                finished, _ = await asyncio.wait(
                    {worker, disconnect, next_notification},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if disconnect in finished:
                    await worker
                    return
                if next_notification in finished:
                    await _send_sse(send, next_notification.result())
                    continue
                next_notification.cancel()
                reply = await worker
                if reply is not None:
                    await _send_sse(send, reply)
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
        except (BrokenPipeError, ConnectionError, OSError):
            cancel_event.set()
            if not worker.done():
                await worker
        finally:
            cancel_event.set()
            tasks: list[asyncio.Task[Any]] = [disconnect, first_notification]
            if next_notification is not None:
                tasks.append(next_notification)
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(worker, return_exceptions=True)
            if pending_key is not None:
                await self._release(pending_key)

    async def _reserve(self, login_key: str, settings: HostedMCPSettings) -> bool:
        async with self._pending_lock:
            count = self._pending_by_login.get(login_key, 0)
            if count >= settings.pending_per_login or self._pending_total >= settings.pending_total:
                return False
            self._pending_by_login[login_key] = count + 1
            self._pending_total += 1
            return True

    async def _release(self, login_key: str) -> None:
        async with self._pending_lock:
            count = self._pending_by_login.get(login_key, 0)
            if count <= 1:
                self._pending_by_login.pop(login_key, None)
            else:
                self._pending_by_login[login_key] = count - 1
            self._pending_total = max(0, self._pending_total - 1)


def _cors_sender(send: Any, origin: str) -> Any:
    async def send_with_cors(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            headers = list(message.get("headers", []))
            headers.extend(
                [
                    (b"access-control-allow-origin", origin.encode("latin-1")),
                    (b"access-control-expose-headers", b"WWW-Authenticate"),
                    (b"vary", b"Origin"),
                ]
            )
            message = {**message, "headers": headers}
        await send(message)

    return send_with_cors


async def _preflight(send: Any, headers: dict[bytes, bytes], has_origin: bool) -> None:
    request_method = headers.get(b"access-control-request-method", b"").decode("latin-1")
    if not has_origin or not request_method:
        await _problem(
            send, 400, "Invalid CORS preflight", "The preflight names no origin or method."
        )
        return
    if request_method.upper() != "POST":
        await _problem(
            send,
            405,
            "Method not allowed",
            "Preflight may only ask for POST.",
            [(b"allow", b"POST, OPTIONS")],
        )
        return

    allowed_headers = {
        b"accept",
        b"authorization",
        b"content-type",
        b"mcp-protocol-version",
    }
    requested_headers = {
        item.strip().lower()
        for item in headers.get(b"access-control-request-headers", b"").split(b",")
        if item.strip()
    }
    if not requested_headers <= allowed_headers:
        await _problem(
            send,
            400,
            "CORS header not allowed",
            "The preflight asks for a header MCP does not accept.",
        )
        return
    await _respond(
        send,
        204,
        b"",
        [
            (b"access-control-allow-methods", b"POST"),
            (
                b"access-control-allow-headers",
                b"Accept, Authorization, Content-Type, MCP-Protocol-Version",
            ),
        ],
    )


def _bearer_token(header: bytes) -> str | None:
    scheme, separator, value = header.decode("latin-1").partition(" ")
    token = value.strip()
    if not separator or scheme.lower() != "bearer" or _BEARER.fullmatch(token) is None:
        return None
    return token


def _accepts_streamable_http(header: bytes) -> bool:
    accepted: set[str] = set()
    for value in header.decode("latin-1").split(","):
        parts = value.split(";")
        media_type = parts[0].strip().lower()
        quality = 1.0
        for parameter in parts[1:]:
            name, separator, raw_value = parameter.strip().partition("=")
            if name.lower() == "q" and separator:
                try:
                    quality = float(raw_value)
                except ValueError:
                    quality = 0.0
        if math.isfinite(quality) and 0 < quality <= 1:
            accepted.add(media_type)
    return {"application/json", "text/event-stream"} <= accepted


async def _read_body(receive: Any, limit: int) -> tuple[bytes | None, bool]:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return None, False
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            return None, True
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks), False


def _parse_message(body: bytes) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    try:
        value = json.loads(body, parse_constant=_reject_json_constant)
    except RecursionError:
        return None, _rpc_error(None, -32700, "The JSON is nested too deeply.")
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None, _rpc_error(None, -32700, "Not JSON.")
    if isinstance(value, list):
        return None, _rpc_error(None, -32600, "JSON-RPC batches are not supported.")
    if (
        not is_object(value)
        or value.get("jsonrpc") != "2.0"
        or not isinstance(value.get("method"), str)
        or not value["method"]
        or ("id" in value and not _valid_request_id(value["id"]))
    ):
        return None, _rpc_error(None, -32600, "Not a JSON-RPC message.")
    return value, None


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"Invalid JSON number {value}.")


def _valid_request_id(value: Any) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, str | int):
        return True
    return isinstance(value, float) and math.isfinite(value)


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


async def _unauthorized(send: Any) -> None:
    await _problem(
        send,
        401,
        "Unauthorized",
        "Send Authorization: Bearer with a valid CLI login.",
        [(b"www-authenticate", b"Bearer")],
    )


async def _too_large(send: Any, max_file_bytes: int) -> None:
    await _problem(
        send,
        413,
        "Request too large",
        f"The maximum file size is {max_file_bytes} bytes.",
    )


async def _problem(
    send: Any,
    status: int,
    title: str,
    detail: str,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> None:
    """A transport error as an RFC 9457 problem document (ADR 0028)."""
    body = json.dumps(
        {"type": "about:blank", "title": title, "status": status, "detail": detail},
        ensure_ascii=False,
    ).encode("utf-8")
    await _respond(
        send,
        status,
        body,
        [(b"content-type", b"application/problem+json"), *(headers or [])],
    )


async def _portal_failure(send: Any, request_id: Any, error: GardenError | OSError) -> None:
    """A portal failure before a tool runs: 401 when the portal refuses the login, otherwise a
    JSON-RPC error. A raw socket or timeout error is reported as the portal being unreachable."""
    if isinstance(error, GardenError) and error.status == 401:
        await _unauthorized(send)
        return
    message = error.title if isinstance(error, GardenError) else "The portal could not be reached."
    await _json_response(send, 502, _rpc_error(request_id, -32000, message))


def _loopback_api_url(scope: dict[str, Any]) -> str | None:
    """The API address for the Engine when none is configured: the port this request arrived on."""
    server = scope.get("server")
    if not server or server[1] is None:
        return None
    return f"http://127.0.0.1:{server[1]}"


async def _json_response(send: Any, status: int, body: dict[str, Any] | None) -> None:
    encoded = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b"null"
    await _respond(send, status, encoded, [(b"content-type", b"application/json")])


async def _respond(send: Any, status: int, body: bytes, headers: list[tuple[bytes, bytes]]) -> None:
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _send_sse(send: Any, body: dict[str, Any]) -> None:
    message = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    event = f"event: message\ndata: {message}\n\n".encode()
    await send({"type": "http.response.body", "body": event, "more_body": True})


async def _watch_disconnect(receive: Any, cancel_event: threading.Event) -> None:
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            cancel_event.set()
            return
