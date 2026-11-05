"""One engine for both ways in: the people's commands and the MCP server.

The engine keeps the portal's contract on disk, refreshes it when a response says the contract
version changed, filters the tools by the person's permissions on every listing (so a newly granted
module shows up on the next command), and runs a tool call through the portal, which checks every
call again.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from garden.errors import ConfirmationRequired, GardenError, NotSignedIn
from garden.jsonio import as_list, as_object, as_text
from garden.portal import CONTRACT_HEADER, PortalClient, Response
from garden.tools import Tool, build_tools

CONTRACT_PATH = "/openapi.json"
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


class Engine:
    def __init__(
        self,
        client: PortalClient,
        cache_dir: Path,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        poll_seconds: float = POLL_SECONDS,
    ) -> None:
        self._client = client
        self._cache_file = cache_dir / "contract.json"
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
        for tool in self.tools():
            if tool.name == name or tool.op_id == name:
                return tool
        return None

    def permissions(self) -> frozenset[str]:
        body = as_object(self._send("GET", ME_PATH).body)
        return frozenset(as_text(item) for item in as_list(body.get("permissions")))

    def me(self) -> dict[str, Any]:
        return as_object(self._send("GET", ME_PATH).body)

    def call(self, tool: Tool, arguments: dict[str, Any], *, notify: Notify | None = None) -> Any:
        path = tool.path
        for param in tool.path_params:
            if param.name not in arguments or arguments[param.name] is None:
                raise GardenError(
                    title="Missing argument",
                    detail=f"{param.name} is required.",
                    fix=f"Pass {param.name}.",
                )
            path = path.replace("{" + param.name + "}", _path_segment(arguments[param.name]))
        query = {param.name: arguments.get(param.name) for param in tool.query_params}
        body = arguments.get("body") if tool.body_schema is not None else None
        if tool.body_required and body is None:
            raise GardenError(
                title="Missing body", detail="This call needs a body.", fix="Pass body."
            )
        method = tool.method.upper()

        def send(headers: dict[str, str] | None = None) -> Any:
            return self._send(method, path, query=query, body=body, headers=headers).body

        try:
            return send()
        except ConfirmationRequired as needed:
            return self._await_approval(needed, send, notify)

    def _await_approval(
        self,
        needed: ConfirmationRequired,
        send: Callable[[dict[str, str] | None], Any],
        notify: Notify | None,
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
            reply = as_object(self._send("GET", status_path).body)
            status = as_text(reply.get("status"))
            if status == "approved":
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

    def _refresh(self) -> None:
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
        self._write_cache(version, openapi)

    def _send(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        if self._client.token is None:
            raise NotSignedIn()
        reply = self._client.request(method, path, query=query, body=body, headers=headers)
        seen = reply.headers.get(CONTRACT_HEADER)
        if seen is not None and seen != self._version:
            self._refresh()
        return reply

    def _read_cache(self) -> tuple[dict[str, Any], str] | None:
        if not self._cache_file.exists():
            return None
        try:
            data = as_object(json.loads(self._cache_file.read_text(encoding="utf-8")))
            return as_object(data["openapi"]), as_text(data["version"])
        except (KeyError, ValueError, TypeError):
            return None

    def _write_cache(self, version: str, openapi: dict[str, Any]) -> None:
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._cache_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": version, "openapi": openapi}), encoding="utf-8")
        os.replace(tmp, self._cache_file)


def _path_segment(value: Any) -> str:
    return quote(str(value), safe="")


def _deadline(expires_at: str, now: float) -> float:
    """The time after which the command stops waiting: the portal's expiry, plus a short grace."""
    try:
        return datetime.fromisoformat(expires_at).timestamp() + WAIT_GRACE_SECONDS
    except ValueError:
        return now + DEFAULT_WAIT_SECONDS
