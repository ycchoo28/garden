"""One engine for both ways in: the people's commands and the MCP server.

The engine keeps the portal's contract on disk, refreshes it when a response says the contract
version changed, filters the tools by the person's permissions on every listing (so a newly granted
module shows up on the next command), and runs a tool call through the portal, which checks every
call again.
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import os
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from garden.auth import load_session
from garden.engine.client import CONTRACT_HEADER, PortalClient, Response, UploadFile
from garden.engine.contract import FormField, Tool, build_tools
from garden.shared import (
    ConfirmationRequired,
    GardenError,
    NotSignedIn,
    Settings,
    as_list,
    as_object,
    as_text,
    home_dir,
    is_object,
    load_settings,
)

CONTRACT_PATH = "/api/openapi.json"
ME_PATH = "/api/users/me"
# The retry of an approved action names the confirmation it spends (see the gate in the portal).
CONFIRMATION_HEADER = "X-Garden-Confirmation"
# How often the command asks the portal whether the person has answered (seconds). The approval
# is seen at the next poll, so this is the most a person waits for the command to go on.
POLL_SECONDS = 1.0
# Once the portal's expiry time has passed, the command waits this long more before it gives up.
WAIT_GRACE_SECONDS = 5.0
# If the portal's expiry time cannot be read, the command waits this long at most.
DEFAULT_WAIT_SECONDS = 600.0

# Tells the person something the command is waiting for, such as the link to approve an action.
Notify = Callable[[str], None]
ContractCache = dict[str, tuple[dict[str, Any], str]]


class Engine:
    def __init__(
        self,
        client: PortalClient,
        cache_dir: Path | None,
        *,
        hosted_files: bool = False,
        max_file_bytes: int | None = None,
        contract_cache: ContractCache | None = None,
        contract_cache_lock: threading.Lock | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._client = client
        self._cache_file = cache_dir / "contract.json" if cache_dir is not None else None
        self._hosted_files = hosted_files
        self.hosted_files = hosted_files
        self._max_file_bytes = max_file_bytes
        self._contract_cache = contract_cache
        self._contract_cache_lock = contract_cache_lock
        self._openapi: dict[str, Any] | None = None
        self._version: str | None = None
        self._sleep = sleep
        self._clock = clock
        self._poll_seconds = poll_seconds

    def contract_version(self) -> str | None:
        self._contract()
        return self._version

    def tools(self) -> list[Tool]:
        # Permissions first: the call may refresh the contract when the portal's version has moved.
        permissions = self.permissions()
        return build_tools(self._contract(), permissions)

    def tool(self, name: str) -> Tool | None:
        for item in self.tools():
            if item.name == name or item.op_id == name:
                return item
        return None

    def find_tool(self, name: str) -> Tool:
        target = self.tool(name)
        if target is None:
            raise GardenError(
                title="Unknown tool",
                detail=f"No tool named '{name}'.",
                fix="Run `garden tools` to list the tools available to you.",
            )
        return target

    def permissions(self) -> frozenset[str]:
        body = as_object(self._send("GET", ME_PATH).body)
        return frozenset(as_text(item) for item in as_list(body.get("permissions")))

    def me(self) -> dict[str, Any]:
        return as_object(self._send("GET", ME_PATH).body)

    def revoke_cli_token(self) -> None:
        self._client.call("DELETE", "/api/agent/cli/token")

    def call(
        self,
        tool: Tool | str,
        arguments: dict[str, Any],
        *,
        notify: Notify | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        target = self.find_tool(tool) if isinstance(tool, str) else tool
        path = target.path
        for param in target.path_params:
            if param.name not in arguments or arguments[param.name] is None:
                raise GardenError(
                    title="Missing argument",
                    detail=f"{param.name} is required.",
                    fix=f"Pass {param.name}.",
                )
            path = path.replace("{" + param.name + "}", _path_segment(arguments[param.name]))
        query = {param.name: arguments.get(param.name) for param in target.query_params}
        body = arguments.get("body") if target.body_schema is not None else None
        if target.body_required and body is None:
            raise GardenError(
                title="Missing body", detail="This call needs a body.", fix="Pass body."
            )
        # The files are read once, before the first request, so a retry after an approval sends the
        # same bytes again without reading the disk a second time.
        form = _form_parts(
            target.form_fields,
            arguments,
            hosted_files=self._hosted_files,
            max_file_bytes=self._max_file_bytes,
        )
        method = target.method.upper()

        def send(headers: dict[str, str] | None = None) -> Any:
            return self._send(method, path, query=query, body=body, form=form, headers=headers).body

        try:
            return send()
        except ConfirmationRequired as needed:
            return self._await_approval(needed, send, notify, cancel_event)

    def _await_approval(
        self,
        needed: ConfirmationRequired,
        send: Callable[[dict[str, str] | None], Any],
        notify: Notify | None,
        cancel_event: threading.Event | None,
    ) -> Any:
        """Wait for the person's answer to the confirmation, then run the action if they approved.

        The retry is the same request plus the confirmation's id, and the portal spends the approval
        on it, so the action runs once. A rejection or an expiry ends the call without the action
        running (ADR 0012).
        """
        if notify is not None:
            notify(
                "Confirmation required. Ask the person to approve this action in the browser:\n"
                f"  {needed.approve_url}\n"
                "It runs once they approve it, and not before."
            )
        status_path = f"/api/agent/my-confirmations/{_path_segment(needed.confirmation_id)}"
        deadline = _deadline(needed.expires_at, self._clock())
        while True:
            _stop_if_cancelled(cancel_event)
            reply = as_object(self._send("GET", status_path).body)
            status = as_text(reply.get("status"))
            if status == "approved":
                _stop_if_cancelled(cancel_event)
                return send({CONFIRMATION_HEADER: needed.confirmation_id})
            if status == "rejected":
                raise GardenError(
                    title="The person rejected this action",
                    detail="It did not run.",
                    fix="Do not try it again unless the person asks for it.",
                )
            if status == "used":
                raise GardenError(
                    title="This approval was already used",
                    detail="The action did not run again.",
                    fix="Run the command again to ask for a new approval.",
                )
            if status == "expired" or self._clock() >= deadline:
                raise GardenError(
                    title="The approval expired",
                    detail="Nobody approved the action in time, so it did not run.",
                    fix="Run the command again to ask for a new approval.",
                )
            if cancel_event is not None:
                cancel_event.wait(self._poll_seconds)
            else:
                self._sleep(self._poll_seconds)

    def _contract(self) -> dict[str, Any]:
        if self._openapi is None:
            cached = self._read_cache()
            if cached is not None:
                self._openapi, self._version = cached
            else:
                self._refresh()
        assert self._openapi is not None
        return self._openapi

    def _refresh(self, expected_version: str | None = None) -> None:
        if self._contract_cache_lock is not None:
            with self._contract_cache_lock:
                self._refresh_unlocked(expected_version)
            return
        self._refresh_unlocked(expected_version)

    def _refresh_unlocked(self, expected_version: str | None) -> None:
        if expected_version and self._contract_cache is not None:
            cached = self._contract_cache.get(expected_version)
            if cached is not None:
                self._openapi, self._version = cached
                return
        reply = self._client.request("GET", CONTRACT_PATH)
        version = reply.headers.get(CONTRACT_HEADER, "")
        openapi = as_object(reply.body)
        if not openapi or not version:
            raise GardenError(
                title="The portal sent no API contract",
                fix="Tell the portal team; the contract header or document is missing.",
            )
        self._openapi = openapi
        self._version = version
        if self._contract_cache is not None:
            self._contract_cache[version] = (openapi, version)
        self._write_cache(version, openapi)

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
        form: list[UploadFile] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        if self._client.token is None:
            raise NotSignedIn()
        reply = self._client.request(
            method, path, query=query, body=body, form=form, headers=headers
        )
        seen = reply.headers.get(CONTRACT_HEADER)
        if seen is not None and seen != self._version:
            self._refresh(seen)
        return reply

    def _read_cache(self) -> tuple[dict[str, Any], str] | None:
        if self._cache_file is None or not self._cache_file.exists():
            return None
        try:
            data = as_object(json.loads(self._cache_file.read_text(encoding="utf-8")))
            return as_object(data["openapi"]), as_text(data["version"])
        except (KeyError, ValueError, TypeError):
            return None

    def _write_cache(self, version: str, openapi: dict[str, Any]) -> None:
        if self._cache_file is None:
            return
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": version, "openapi": openapi}), encoding="utf-8")
        os.replace(tmp, self._cache_file)


def connect(
    *,
    portal_url: str | None = None,
    token: str | None = None,
    cache_dir: Path | None = None,
    settings: Settings | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
    poll_seconds: float = POLL_SECONDS,
) -> Engine:
    if settings is not None:
        active_settings: Settings | None = settings
    else:
        try:
            active_settings = load_settings()
        except GardenError:
            if portal_url is not None:
                active_settings = None
            else:
                raise

    if active_settings is not None and portal_url is not None:
        active_settings = replace(active_settings, portal_url=portal_url)
    session = load_session(active_settings) if token is None and active_settings else None

    resolved_token = (
        token if token is not None else (session.token if session is not None else None)
    )
    fallback_portal = active_settings.portal_url if active_settings else ""
    resolved_url = portal_url or fallback_portal
    resolved_cache = cache_dir or (
        active_settings.cache_dir if active_settings else home_dir() / "cache"
    )
    return Engine(
        PortalClient(resolved_url, token=resolved_token),
        cache_dir=resolved_cache,
        sleep=sleep,
        clock=clock,
        poll_seconds=poll_seconds,
    )


def _path_segment(value: Any) -> str:
    return quote(str(value), safe="")


def _form_parts(
    fields: tuple[FormField, ...],
    arguments: dict[str, Any],
    *,
    hosted_files: bool = False,
    max_file_bytes: int | None = None,
) -> list[UploadFile] | None:
    """The multipart body for a call, or None when the operation has no form."""
    if not fields:
        return None
    parts: list[UploadFile] = []
    for field in fields:
        value = arguments.get(field.param.name)
        if value is None:
            if field.param.required:
                raise GardenError(
                    title="Missing argument",
                    detail=f"{field.param.name} is required.",
                    fix=f"Pass {field.param.name}.",
                )
            continue
        if field.binary:
            parts.append(
                _memory_file_part(field, value, max_file_bytes)
                if hosted_files
                else _file_part(field, value)
            )
        else:
            parts.append(UploadFile(name=field.name, data=_text(value).encode("utf-8")))
    return parts


def _memory_file_part(field: FormField, value: Any, max_file_bytes: int | None) -> UploadFile:
    argument = field.param.name
    if not is_object(value) or set(value) != {"filename", "content_type", "data_base64"}:
        raise GardenError(
            title="Invalid hosted file",
            detail=f"{argument} must contain filename, content_type and data_base64.",
            fix="Send the file content as base64; filesystem paths are not accepted.",
        )
    filename, content_type, encoded = (
        value.get("filename"),
        value.get("content_type"),
        value.get("data_base64"),
    )
    if (
        not isinstance(filename, str)
        or not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or PurePosixPath(filename).name != filename
        or PureWindowsPath(filename).name != filename
        or ":" in filename
    ):
        raise GardenError(
            title="Invalid hosted filename",
            detail="A hosted file name must be a plain file name, not a path.",
            fix="Pass only the file name, without a directory.",
        )
    if (
        not isinstance(content_type, str)
        or not content_type
        or any(char in content_type for char in "\r\n")
    ):
        raise GardenError(
            title="Invalid hosted content type",
            detail="The file content type must be a single non-empty value.",
            fix="Pass a MIME type such as application/pdf.",
        )
    if not isinstance(encoded, str):
        raise GardenError(
            title="Invalid hosted file data",
            detail="data_base64 must be a base64 string.",
            fix="Encode the file bytes as base64.",
        )
    if max_file_bytes is not None:
        padding = len(encoded) - len(encoded.rstrip("="))
        decoded_size = (len(encoded) * 3 // 4) - padding
        if decoded_size > max_file_bytes:
            raise GardenError(
                title="The file is too large",
                detail=f"The maximum file size is {max_file_bytes} bytes.",
                fix="Use a smaller file.",
                status=413,
            )
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise GardenError(
            title="Invalid hosted file data",
            detail="data_base64 is not valid base64.",
            fix="Encode the file bytes as standard base64.",
        ) from None
    if max_file_bytes is not None and len(data) > max_file_bytes:
        raise GardenError(
            title="The file is too large",
            detail=f"The maximum file size is {max_file_bytes} bytes.",
            fix="Use a smaller file.",
            status=413,
        )
    return UploadFile(name=field.name, data=data, filename=filename, content_type=content_type)


def _stop_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise GardenError(
            title="Confirmation wait stopped",
            detail="The caller disconnected, so the action did not run.",
            fix="Reconnect and ask the person to confirm again.",
        )


def _file_part(field: FormField, value: Any) -> UploadFile:
    argument = field.param.name
    if not isinstance(value, str) or not value:
        raise GardenError(
            title="The file path is missing",
            detail=f"{argument} must be the path to a file on this computer.",
            fix=f"Pass {argument} as the path of the file to upload.",
        )
    path = Path(value).expanduser()
    if path.is_dir():
        raise GardenError(
            title="The path is a folder",
            detail=f"{value} is a folder, so there is no file to upload.",
            fix=f"Pass {argument} as the path of one file inside it.",
        )
    if not path.is_file():
        raise GardenError(
            title="The file does not exist",
            detail=f"No file at {value}.",
            fix=f"Check the spelling of {argument}, or pass an absolute path to the file.",
        )
    try:
        data = path.read_bytes()
    except OSError as error:
        raise GardenError(
            title="The file cannot be read",
            detail=f"{value}: {error.strerror or error}",
            fix="Check that this login may read the file, then try again.",
        ) from error
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return UploadFile(name=field.name, data=data, filename=path.name, content_type=content_type)


def _text(value: Any) -> str:
    """A form field's text: booleans as true or false, and objects and lists as JSON."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value)
    return str(value)


def _deadline(expires_at: str, now: float) -> float:
    """The time after which the command stops waiting: the portal's expiry, plus a short grace."""
    try:
        expiry = datetime.fromisoformat(expires_at)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return expiry.timestamp() + WAIT_GRACE_SECONDS
    except ValueError:
        return now + DEFAULT_WAIT_SECONDS
