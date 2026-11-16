"""The one harness adapter used by eval trials."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import cast

import yaml

from tests.evals.tasks import ClaimDraftTask, ReferenceSolution
from tests.shared.garden_cli import store_token
from tests.shared.mcp import Mcp
from tests.shared.paths import AGENT_ROOT

JsonObject = dict[str, object]


def _as_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


class DshHarnessAdapter:
    """Prepare dsh's isolated home and workspace, then run a scripted MCP reference solution."""

    def __init__(
        self,
        task: ClaimDraftTask,
        *,
        portal_url: str,
        home: Path,
        workspace: Path,
        acting_token: str,
    ) -> None:
        self.task = task
        self.home = home
        self.workspace = workspace
        self.tool_calls = 0
        self.client: Mcp | None = None
        self.home.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)

        secret_file = self.home / "secrets.json"
        env = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
        env.update(
            {
                "HOME": str(self.home),
                "GARDEN_HOME": str(self.home / ".garden"),
                "GARDEN_API_URL": portal_url,
                "GARDEN_SECRET_STORE": f"file:{secret_file}",
                "PYTHONIOENCODING": "utf-8",
                "PYTHONPATH": str(AGENT_ROOT / "src"),
            }
        )
        store_token(env, portal_url, acting_token, device=f"eval-{task.id}")

        bin_dir = self.home / ".local" / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        launcher = bin_dir / "garden"
        launcher.write_text(
            f'#!/bin/sh\nexec {shlex.quote(sys.executable)} -m garden "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o700)

        dsh_home = self.home / ".dsh"
        dsh_home.mkdir(parents=True, exist_ok=True)
        patch = [
            {
                "insert": [
                    {
                        "id": "mcp-garden",
                        "name": "@deepseek-ai/dsh-mcp-client",
                        "config": {
                            "serverName": "garden",
                            "transport": "stdio",
                            "command": str(launcher),
                            "args": ["mcp"],
                            "env": {
                                "GARDEN_API_URL": portal_url,
                                "GARDEN_SECRET_STORE": f"file:{secret_file}",
                                "GARDEN_HOME": str(self.home / ".garden"),
                                "PYTHONPATH": str(AGENT_ROOT / "src"),
                            },
                        },
                    }
                ]
            }
        ]
        (dsh_home / "cordis.patch.yml").write_text(
            yaml.safe_dump(patch, sort_keys=False), encoding="utf-8"
        )
        (self.workspace / "receipt.pdf").write_bytes(b"%PDF-1.4\nGarden reference receipt\n%%EOF\n")
        self.client = Mcp(launcher, env, cwd=self.workspace)

    def call(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        if self.client is None:
            raise RuntimeError("The Garden MCP client is not ready.")
        self.tool_calls += 1
        reply = cast(
            JsonObject,
            self.client.request("tools/call", {"name": name, "arguments": arguments}),
        )
        if "error" in reply:
            raise RuntimeError(f"Garden MCP returned an error for {name}.")
        result = _as_object(reply.get("result"))
        if result is None:
            raise RuntimeError(f"Garden MCP returned no result for {name}.")
        if result.get("isError") is True:
            details = result.get("content")
            raise RuntimeError(f"Garden MCP tool {name} failed: {details!r}")
        structured = _as_object(result.get("structuredContent"))
        if structured is not None:
            return structured
        content_value = result.get("content")
        if isinstance(content_value, list):
            content = cast(list[object], content_value)
            for item_value in content:
                item = _as_object(item_value)
                text = item.get("text") if item is not None else None
                if isinstance(text, str):
                    try:
                        decoded = cast(object, json.loads(text))
                    except json.JSONDecodeError:
                        continue
                    decoded_object = _as_object(decoded)
                    if decoded_object is not None:
                        return decoded_object
        raise RuntimeError(f"Garden MCP returned no structured result for {name}.")

    def run_reference(self, solution: ReferenceSolution) -> str:
        return solution(self, self.task)

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
