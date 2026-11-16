"""`garden mcp` over stdin and stdout, the way an MCP client talks to it."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from tests.shared.transcript import StepFailed


class Mcp:
    """`garden mcp` over stdin and stdout, the way an MCP client talks to it."""

    def __init__(self, launcher: Path, env: dict[str, str]) -> None:
        self.process = subprocess.Popen(
            [str(launcher), "mcp"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.next_id = 1
        # Log notifications, such as a critical call's approval link, that arrive before a reply.
        self.notices: list[dict[str, Any]] = []
        self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "agent-e2e", "version": "0"},
            },
        )
        self.notify("notifications/initialized")

    def notify(self, method: str) -> None:
        self._write({"jsonrpc": "2.0", "method": method})

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.reply(self.start(method, params))

    def start(self, method: str, params: dict[str, Any] | None = None) -> int:
        """Send a request and return its id, without waiting for the reply."""
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._write(message)
        return request_id

    def reply(self, request_id: int) -> dict[str, Any]:
        """The reply to one request. Notifications read on the way are kept in `notices`."""
        while True:
            message = self._read()
            if message.get("id") == request_id:
                return message
            self.notices.append(message)

    def notice(self) -> dict[str, Any]:
        """The next log notification, waiting for one if none has arrived yet."""
        for index, message in enumerate(self.notices):
            if message.get("method") == "notifications/message":
                return self.notices.pop(index)
        while True:
            message = self._read()
            if message.get("method") == "notifications/message":
                return message
            self.notices.append(message)

    def _read(self) -> dict[str, Any]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            raise StepFailed("garden mcp closed its output before it answered.")
        return json.loads(line)  # type: ignore[no-any-return]

    def _write(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def close(self) -> None:
        if self.process.stdin is not None:
            self.process.stdin.close()
        self.process.wait(timeout=60)
