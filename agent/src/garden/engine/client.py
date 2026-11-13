"""HTTP calls to the portal, with its error shape and its version headers.

Every response carries the contract version and the minimum CLI version. The client returns both,
so the engine can refresh the tool list and refuse to run an old `garden`.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from garden import __version__
from garden.shared import (
    CliTooOld,
    ConfirmationRequired,
    GardenError,
    as_list,
    as_object,
    as_text,
    is_object,
)

CONTRACT_HEADER = "x-garden-contract-version"
MIN_CLI_HEADER = "x-garden-min-cli-version"
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: Any


@dataclass(frozen=True)
class UploadFile:
    """One part of a multipart/form-data body. A file part has a filename and a content type."""

    name: str
    data: bytes
    filename: str | None = None
    content_type: str | None = None


class HTTPTransport(Protocol):
    """The HTTP boundary used by the Engine to reach the portal."""

    def send(self, request: urllib.request.Request, timeout: float) -> Response: ...


class UrllibTransport:
    """The normal CLI transport. Hosted deployments can provide a different implementation."""

    def send(self, request: urllib.request.Request, timeout: float) -> Response:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                return Response(reply.status, _lower_headers(reply.headers), _decode(reply.read()))
        except urllib.error.HTTPError as error:
            problem = _decode(error.read())
            raise _problem_error(error.code, problem) from None
        except urllib.error.URLError as error:
            raise GardenError(
                title="The portal cannot be reached",
                detail=str(error.reason),
                fix="Check the network, and that the portal address is right, then try again.",
            ) from error


def encode_multipart(parts: Sequence[UploadFile]) -> tuple[bytes, str]:
    """The body and its Content-Type. The boundary is random, so no part's bytes can end it."""
    boundary = uuid.uuid4().hex
    chunks: list[bytes] = []
    for part in parts:
        disposition = f'form-data; name="{part.name}"'
        if part.filename is not None:
            # A quote or a line break in a name would end the header early, so each becomes "_".
            safe = "".join("_" if char in '"\r\n' else char for char in part.filename)
            disposition += f'; filename="{safe}"'
        header = f"--{boundary}\r\nContent-Disposition: {disposition}\r\n"
        if part.content_type is not None:
            header += f"Content-Type: {part.content_type}\r\n"
        chunks.append(header.encode("utf-8") + b"\r\n" + part.data + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _lower_headers(message: Any) -> dict[str, str]:
    """Header names are case-insensitive, and servers send them in their own case, often lower."""
    return {name.lower(): value for name, value in message.items()}


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.strip().split("."))


class PortalClient:
    def __init__(
        self,
        portal_url: str,
        token: str | None = None,
        *,
        transport: HTTPTransport | None = None,
    ) -> None:
        self.portal_url = portal_url.rstrip("/")
        self.token = token
        self._transport = transport or UrllibTransport()

    def url(self, path: str, query: dict[str, Any] | None = None) -> str:
        full = f"{self.portal_url}{path}"
        if query:
            pairs = [
                (key, _query_value(value)) for key, value in query.items() if value is not None
            ]
            if pairs:
                full = f"{full}?{urllib.parse.urlencode(pairs)}"
        return full

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
        form: Sequence[UploadFile] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        request_headers = {
            "Accept": "application/json",
            "User-Agent": f"garden/{__version__}",
            **(headers or {}),
        }
        data: bytes | None = None
        if form is not None:
            data, request_headers["Content-Type"] = encode_multipart(form)
        elif body is not None:
            request_headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if self.token:
            request_headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(
            self.url(path, query), data=data, method=method, headers=request_headers
        )
        response = self._transport.send(request, TIMEOUT_SECONDS)
        check_min_version(response.headers)
        return response

    call = request


def check_min_version(headers: dict[str, str]) -> None:
    minimum = headers.get(MIN_CLI_HEADER)
    if minimum is None:
        return
    if parse_version(__version__) < parse_version(minimum):
        raise CliTooOld(__version__, minimum)


def _query_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return raw.decode("utf-8", errors="replace")


_FIXES: dict[int, str] = {
    401: "Run `garden login`. Your Agent login may have been revoked.",
    403: "Your access does not include this. Ask the person to give you the permission in Garden.",
    404: "Check the id. List the items first to find the right one.",
    409: "The item changed or the approval was already used. Read it again, then retry.",
    413: "Use a smaller file. The problem says how large a file may be.",
    428: "Ask the person to approve this action at the link, in the browser. The command waits.",
    422: "Fix the fields listed under errors, then retry.",
    429: "Wait a minute and try again. The portal limits requests for each Agent login.",
}


def _problem_error(status: int, problem: Any) -> GardenError:
    body = as_object(problem)
    if status == 428 and "confirmation_id" in body:
        return ConfirmationRequired(
            title=as_text(body.get("title"), "Confirmation Required"),
            detail=as_text(body.get("detail")) or None,
            status=status,
            fix=_FIXES[428],
            confirmation_id=as_text(body.get("confirmation_id")),
            approve_url=as_text(body.get("confirmation_url")),
            expires_at=as_text(body.get("expires_at")),
        )
    errors = [
        {"field": as_text(item.get("field")), "message": as_text(item.get("message"))}
        for item in as_list(body.get("errors"))
        if is_object(item)
    ]
    fix = _FIXES.get(
        status,
        "Try again. If it repeats, tell the portal team this status and the command you ran.",
    )
    return GardenError(
        title=as_text(body.get("title"), f"HTTP {status}"),
        detail=as_text(body.get("detail")) or None,
        status=status,
        fix=fix,
        errors=errors,
    )
