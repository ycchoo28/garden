"""The one harness adapter used by eval trials."""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from tests.evals.task_types import EvalTask, ReferenceSolution
from tests.shared.browser import decide_confirmation
from tests.shared.garden_cli import store_token
from tests.shared.mcp import Mcp
from tests.shared.paths import AGENT_ROOT
from tests.shared.transcript import StepFailed

JsonObject = dict[str, object]


def _as_object(value: object) -> JsonObject | None:
    if not isinstance(value, dict):
        return None
    return cast(JsonObject, value)


ConfirmationDecision = Literal["approve", "deny", "none"]
ConfirmationResponder = Callable[[str, Literal["approve", "deny"]], None]
CONFIRMATION_LINK = re.compile(r"https?://\S+/agent/confirmations/[0-9a-f-]{36}")


class ConfirmationPolicy:
    """Apply one task's expected confirmation decision and reject any extra request."""

    def __init__(
        self,
        *,
        task_id: str,
        expected_decision: ConfirmationDecision,
        public_url: str,
        respond: ConfirmationResponder,
    ) -> None:
        self.task_id = task_id
        self.expected_decision = expected_decision
        self.public_url = public_url.rstrip("/")
        self.respond = respond
        self.decisions: list[Literal["approve", "deny"]] = []
        self.failure_reason: str | None = None

    def on_notification(self, message: dict[str, object]) -> None:
        if message.get("method") != "notifications/message":
            return
        params = _as_object(message.get("params"))
        data = params.get("data") if params is not None else None
        match = CONFIRMATION_LINK.search(data) if isinstance(data, str) else None
        if match is None:
            raise StepFailed(
                f"Confirmation for task {self.task_id!r} did not include a usable browser link."
            )
        link = match.group(0)
        if not link.startswith(f"{self.public_url}/agent/confirmations/"):
            raise StepFailed(
                f"Confirmation for task {self.task_id!r} did not point to the trial web app."
            )

        if self.failure_reason is not None:
            decision: Literal["approve", "deny"] = "deny"
            self.failure_reason = (
                f"Unexpected additional confirmation for task {self.task_id!r}; "
                "the runner declined it."
            )
        elif self.expected_decision == "none":
            decision = "deny"
            self.failure_reason = (
                f"Unexpected confirmation for task {self.task_id!r}; the runner declined it."
            )
        elif self.decisions:
            decision = "deny"
            self.failure_reason = (
                f"Unexpected additional confirmation for task {self.task_id!r}; "
                "the runner declined it."
            )
        else:
            decision = "approve" if self.expected_decision == "approve" else "deny"

        self.respond(link, decision)
        if self.failure_reason is None:
            self.decisions.append(decision)

    def verify(self) -> None:
        if self.failure_reason is not None:
            raise StepFailed(self.failure_reason)
        if self.expected_decision != "none" and self.decisions != [self.expected_decision]:
            raise StepFailed(
                f"Expected one {self.expected_decision} confirmation for task {self.task_id!r}, "
                f"received {len(self.decisions)}."
            )


class DshHarnessAdapter:
    """Prepare dsh's isolated home and workspace, then run a scripted MCP reference solution."""

    def __init__(
        self,
        task: EvalTask,
        *,
        portal_url: str,
        database: str,
        public_url: str | None = None,
        oidc_url: str | None = None,
        home: Path,
        workspace: Path,
        media: Path | None = None,
        acting_token: str,
        person_session: str | None = None,
    ) -> None:
        self.task = task
        self.portal_url = portal_url
        self.database = database
        self.oidc_url = oidc_url
        self.acting_token = acting_token
        self.person_session = person_session
        self.home = home
        self.workspace = workspace
        self.media = media
        self.tool_calls = 0
        self.client: Mcp | None = None
        self.home.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        if media is not None:
            media.mkdir(parents=True, exist_ok=True)
        self.confirmation_policy = (
            ConfirmationPolicy(
                task_id=task.id,
                expected_decision=task.confirmation,
                public_url=public_url,
                respond=self._respond_to_confirmation,
            )
            if public_url is not None
            else None
        )

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
        (self.workspace / "second-receipt.pdf").write_bytes(
            b"%PDF-1.4\nGarden second reference receipt\n%%EOF\n"
        )
        self.client = Mcp(launcher, env, cwd=self.workspace)

    def call(self, name: str, arguments: dict[str, object]) -> object:
        if self.client is None:
            raise RuntimeError("The Garden MCP client is not ready.")
        self.tool_calls += 1
        reply = cast(
            JsonObject,
            self.client.request(
                "tools/call",
                {"name": name, "arguments": arguments},
                on_notification=(
                    self.confirmation_policy.on_notification
                    if self.confirmation_policy is not None
                    else None
                ),
            ),
        )
        if (
            self.confirmation_policy is not None
            and self.confirmation_policy.failure_reason is not None
        ):
            raise StepFailed(self.confirmation_policy.failure_reason)
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
        structured_value = result.get("structuredContent")
        if isinstance(structured_value, list):
            return cast(object, structured_value)
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
                    if isinstance(decoded, list):
                        return cast(list[object], decoded)
        raise RuntimeError(f"Garden MCP returned no structured result for {name}.")

    def person_call(self, method: str, path: str, body: dict[str, object]) -> dict[str, object]:
        if self.person_session is None:
            raise RuntimeError("The trial has no person session for this API call.")
        request = Request(
            f"{self.portal_url.rstrip('/')}{path}",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Cookie": f"garden_session={self.person_session}",
            },
            method=method,
        )
        try:
            with urlopen(request, timeout=30) as response:
                result = _as_object(cast(object, json.loads(response.read())))
        except HTTPError as error:
            raise RuntimeError(f"Person API call returned HTTP {error.code} for {path}.") from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Could not make a person API call to {path}: {error}.") from error
        if result is None:
            raise RuntimeError("Person API call returned no JSON object.")
        return result

    def _respond_to_confirmation(self, link: str, decision: Literal["approve", "deny"]) -> None:
        if self.media is None or self.oidc_url is None:
            raise StepFailed("The confirmation browser is not configured for this trial.")
        result = decide_confirmation(
            link,
            self.media,
            self.task.asking_person,
            decision,
            oidc_url=self.oidc_url,
        )
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip()
            raise StepFailed(
                f"The scripted browser could not {decision} the confirmation for task "
                f"{self.task.id!r}: {detail[-1000:]}"
            )

    def run_reference(self, solution: ReferenceSolution) -> str:
        return solution(self, self.task)

    def verify_confirmations(self) -> None:
        if self.confirmation_policy is not None:
            self.confirmation_policy.verify()

    def close(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
