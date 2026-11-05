"""The local end-to-end run of garden's agent contract (`just agent-e2e`).

It stands up its own portal, so the dev stack is never touched: a fresh Postgres database whose name
ends in `_test` on the dev Postgres container, the API on 127.0.0.1:56000 and the web app on
127.0.0.1:56173 (`just demo` uses 55000 and 55173), and a fake OpenAI-compatible model upstream on a
free port. The model is the portal's (ADR 0057): the gateway forwards to that fake upstream, and the
upstream key never reaches the laptop.

The run publishes the installer's files into a temporary folder the portal serves, runs
the installer
with a fresh HOME as `curl ... | sh` does, and approves every sign-in and every critical action in a
headless browser. It then drives the CLI, the MCP server and dsh the way a person's laptop
does, checks
the gateway, signs out, and stops everything it started.

Output goes to agent/dist/e2e (the transcript, the logs and the screenshots), which is not
committed.
Secrets never reach the transcript: the CLI token is redacted.

With --serve (`just agent-e2e-serve`) the same stack stays up for a hand test from Windows. It
skips the Linux install, sign-in and dsh steps, prints the installer one-liner and the steps for
PowerShell, and stops on Ctrl+C, which tears down its processes and drops its database. Its output
goes to agent/dist/e2e-serve.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
OUT = ROOT / "dist" / "e2e"
SERVE_OUT = ROOT / "dist" / "e2e-serve"
# The installer files serve mode checks through the web app, as a Windows laptop fetches them.
WINDOWS_FILES = ("install.ps1", "dsh-bundle-windows-x64.tar.gz")
API_PORT = 56000
WEB_PORT = 56173
OIDC_URL = "http://127.0.0.1:55081"
PG_URL = "postgresql+psycopg://garden:garden-dev@127.0.0.1:55432"
PG_PORT = 55432
PORTAL = f"http://127.0.0.1:{API_PORT}"
WEB = f"http://localhost:{WEB_PORT}"
GATEWAY_PATH = "/api/agent/model/v1"
MODEL = "e2e-fake-model"
PERSON_NAME = "Dev Admin"
PERSON_SUBJECT = "dev.admin@fernhill.example"
# The tool that waits for the person: deleting a file is a critical action
# (confirm=True in the API).
CRITICAL_TOOL = "files_file_delete"
PROBE_SHA = "0" * 64
REPLY_MARKER = "E2E-FAKE-REPLY"
PING = {
    "messages": [{"role": "user", "content": "Reply with the single word: pong"}],
    "max_tokens": 16,
}
DB_NAME = re.compile(r"^garden_e2e\d+_test$")
LINK = re.compile(r"https?://\S+/agent/confirmations/[0-9a-f-]{36}")


class Run:
    """Collects the transcript and the checks. Every secret goes through `redact` before it is
    kept."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failures: list[str] = []
        self.secrets: list[str] = []

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[redacted]")
        return text

    def heading(self, title: str) -> None:
        self.say(f"\n## {title}\n")

    def say(self, text: str) -> None:
        text = self.redact(text)
        self.lines.append(text)
        print(text, flush=True)

    def record(self, command: str, output: str, code: int | None = None) -> None:
        shown = self.redact(output.strip())
        if len(shown) > 1500:
            shown = shown[:1500] + "\n[... truncated ...]"
        status = "" if code is None else f" (exit {code})"
        self.say(f"$ {self.redact(command)}{status}\n```\n{shown}\n```")

    def check(self, ok: bool, what: str) -> None:
        self.say(f"- {'PASS' if ok else 'FAIL'}: {what}")
        if not ok:
            self.failures.append(what)


class StepFailed(Exception):
    """A step cannot go on, so the run stops here and the transcript says why."""


class Interrupted(Exception):
    """A stop signal arrived while the stack was up, so the run tears down and ends."""


@dataclass
class Services:
    """Every process this run starts. They run in their own sessions, so each one can be stopped
    with its group."""

    procs: list[subprocess.Popen[bytes]] = field(default_factory=list)

    def start(
        self, args: list[str], *, cwd: Path, env: dict[str, str], log: Path
    ) -> subprocess.Popen[bytes]:
        handle = log.open("w", encoding="utf-8")
        proc = subprocess.Popen(
            args, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True
        )
        self.procs.append(proc)
        return proc

    def stop_all(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for proc in self.procs:
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=20)


class FakeUpstream(BaseHTTPRequestHandler):
    """A fixed OpenAI-compatible model. With tools on offer it asks for one critical delete,
    then answers with a marker and what the tool returned. Without tools it answers `pong`.
    It keeps a count of calls."""

    calls: ClassVar[list[dict[str, Any]]] = []
    # Off in serve mode: the fake then answers with text and never asks for the critical delete.
    critical_call: ClassVar[bool] = True
    # The file the critical delete names: the probe, or the file the dsh step uploaded.
    critical_sha: ClassVar[str] = PROBE_SHA

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        FakeUpstream.calls.append(body)
        reply = self.answer(body)
        if body.get("stream") is True:
            asked = (body.get("stream_options") or {}).get("include_usage") is True
            data = self.as_stream(reply, usage=asked)
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
        else:
            data = json.dumps(reply).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def as_stream(reply: dict[str, Any], usage: bool) -> bytes:
        """The same answer as server-sent events, the way an OpenAI-compatible upstream
        streams it. The usage comes in a last chunk, only when the caller asked for it."""
        choice = reply["choices"][0]
        message = choice["message"]
        delta: dict[str, Any] = {"role": "assistant"}
        if message.get("content") is not None:
            delta["content"] = message["content"]
        if message.get("tool_calls"):
            delta["tool_calls"] = [{"index": 0, **message["tool_calls"][0]}]
        base = {"id": reply["id"], "object": "chat.completion.chunk", "model": reply["model"]}
        events = [
            {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
            },
        ]
        if usage:
            events.append({**base, "choices": [], "usage": reply["usage"]})
        lines = [f"data: {json.dumps(event)}\n\n" for event in events]
        return "".join([*lines, "data: [DONE]\n\n"]).encode("utf-8")

    @staticmethod
    def answer(body: dict[str, Any]) -> dict[str, Any]:
        tools = body.get("tools") or []
        messages = body.get("messages") or []
        tool_results = [message for message in messages if message.get("role") == "tool"]
        message: dict[str, Any]
        finish = "stop"
        if tools and not tool_results and FakeUpstream.critical_call:
            name = next(
                (
                    str(tool["function"]["name"])
                    for tool in tools
                    if str(tool["function"]["name"]).endswith(CRITICAL_TOOL)
                ),
                CRITICAL_TOOL,
            )
            arguments = json.dumps({"sha256": FakeUpstream.critical_sha})
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_e2e_1",
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                ],
            }
            finish = "tool_calls"
        elif tools:
            result = (
                str(tool_results[-1].get("content", ""))[:160] if tool_results else "no tool call"
            )
            message = {
                "role": "assistant",
                "content": f"{REPLY_MARKER}: the portal answered {result}",
            }
        else:
            message = {"role": "assistant", "content": "pong"}
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", MODEL),
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20},
        }

    def log_message(self, format: str, *args: Any) -> None:
        return


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def require_port_free(port: int, what: str) -> None:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    except OSError:
        return
    raise StepFailed(
        f"Port {port} ({what}) is already in use. Stop that process, or pick another port."
    )


def wait_for(url: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5):
                return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise StepFailed(f"Nothing answered at {url} within {int(timeout)} seconds.")


def upload_file(token: str, name: str, data: bytes) -> tuple[int, str]:
    """One multipart upload to the portal, as the CLI login. Returns the status and the body."""
    boundary = uuid.uuid4().hex
    body = (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
            "Content-Type: text/plain\r\n\r\n"
        ).encode()
        + data
        + f"\r\n--{boundary}--\r\n".encode()
    )
    request = urllib.request.Request(f"{PORTAL}/api/files", data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _tokens_today(token: str) -> int:
    """The person's total tokens today, as the gateway counts them."""
    status, text = api("GET", "/api/agent/model/my-usage", token=token)
    return int(json.loads(text).get("total_tokens", 0)) if status == 200 else 0


def api(method: str, path: str, token: str | None = None, body: Any = None) -> tuple[int, str]:
    """One portal call. Returns the status and the body, and never raises on an HTTP error."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(f"{PORTAL}{path}", data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if token is not None:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def psql(database: str, sql: str) -> str:
    """One query in the Compose Postgres container, for the database this run owns."""
    command = [
        "docker",
        "compose",
        "-f",
        str(REPO / "dev" / "compose.yaml"),
        "exec",
        "-T",
        "postgres",
        "psql",
        "-U",
        "garden",
        "-d",
        database,
        "-v",
        "ON_ERROR_STOP=1",
        "-tAc",
        sql,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)
    if result.returncode != 0:
        raise StepFailed(f"psql failed: {result.stderr.strip()}")
    return result.stdout.strip()


def approve(script: str, link: str, media: Path) -> subprocess.CompletedProcess[str]:
    """Approves a login or an action in a headless browser, through its own script."""
    args = ["node", str(ROOT / "scripts" / script), link, str(media), PERSON_NAME, PERSON_SUBJECT]
    return subprocess.run(args, capture_output=True, text=True, timeout=240, check=False)


def read_token(secrets_file: Path) -> str:
    """The CLI token from the test-only secret file. It is returned, never printed."""
    data = json.loads(secrets_file.read_text(encoding="utf-8"))
    return str(json.loads(data[PORTAL])["token"])


def sign_in_headless(
    command: list[str], env: dict[str, str], media: Path, run: Run, label: str
) -> tuple[int, str]:
    """Runs `garden login` with its link printed, approves the link in a headless browser, and waits
    for the CLI to finish. Returns the exit code and the CLI's output."""
    proc = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    assert proc.stdout is not None
    link = ""
    collected: list[str] = []
    for line in proc.stdout:
        collected.append(line)
        if line.startswith("  http") and not link:
            link = line.strip()
            run.secrets.append(link)
            approval = approve("approve-login.cjs", link, media)
            run.record(
                f"{label}: approve-login.cjs <login link> (headless)",
                approval.stdout + approval.stderr,
                approval.returncode,
            )
            run.check(
                approval.returncode == 0, f"{label}: the login was approved in the headless browser"
            )
    code = proc.wait(timeout=300)
    output = "".join(collected)
    if not link:
        raise StepFailed(f"{label}: the CLI printed no login link:\n{output}")
    return code, output


def run_dsh(
    command: list[str], env: dict[str, str], media: Path, run: Run
) -> tuple[int, str, str, list[subprocess.CompletedProcess[str]]]:
    """Runs dsh, and approves each critical action it announces, as the person would in the browser.
    The approval link arrives while dsh's call waits, so the approval runs alongside dsh."""
    proc = subprocess.Popen(
        command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    assert proc.stdout is not None and proc.stderr is not None
    out: list[str] = []
    err: list[str] = []
    approvals: list[subprocess.CompletedProcess[str]] = []
    seen: set[str] = set()
    lock = threading.Lock()

    def watch(stream: Any, sink: list[str]) -> None:
        for line in stream:
            with lock:
                sink.append(line)
            for match in LINK.finditer(line):
                link = match.group(0)
                if link in seen:
                    continue
                seen.add(link)
                run.secrets.append(link)
                approvals.append(approve("approve-confirmation.cjs", link, media))

    readers = [
        threading.Thread(target=watch, args=(proc.stdout, out), daemon=True),
        threading.Thread(target=watch, args=(proc.stderr, err), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        code = proc.wait(timeout=600)
    finally:
        for reader in readers:
            reader.join(timeout=300)
    return code, "".join(out), "".join(err), approvals


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


def dsh_overlay(launcher: Path, secrets_file: Path, installed_patch: Path, out: Path) -> Path:
    """dsh's patch for this run: the installed one, with the garden server given the test-only
    file store.

    dsh passes only some environment variables to an MCP server, and the secret store variable
    is one of those it drops. On a laptop the server reads the keychain and needs no overlay.
    The model and the login stay as the installer and `garden dsh` set them: the token is in
    dsh's environment, never here.
    """
    document = yaml.safe_load(installed_patch.read_text(encoding="utf-8"))
    rows = document[0]["insert"]
    for row in rows:
        if row.get("id") == "mcp-garden":
            row["config"]["command"] = str(launcher)
            row["config"]["env"] = {"GARDEN_SECRET_STORE": f"file:{secrets_file}"}
    path = out / "dsh-e2e-overlay.yml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def main(serve: bool = False) -> int:
    run = Run()
    run.say("# Agent end-to-end transcript\n")
    run.say(
        f"Portal: {PORTAL}. Web: {WEB}. "
        f"Run started {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}."
    )
    out = SERVE_OUT if serve else OUT
    FakeUpstream.critical_call = not serve
    services = Services()
    database = f"garden_e2e{int(time.time())}_test"
    work = Path(tempfile.mkdtemp(prefix="garden-e2e-"))
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeUpstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    status = 1
    if serve:
        install_stop_handlers()
    try:
        status = _run(run, services, database, work, upstream, out, serve)
    except Interrupted as stop:
        run.say(f"\nStopped by {stop}. Tearing down.")
        status = 0
    except StepFailed as failure:
        run.say(f"\nStopped: {failure}")
        run.failures.append(str(failure))
    finally:
        if serve:
            # A second Ctrl+C must not interrupt the teardown and leave processes behind.
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
        services.stop_all()
        upstream.shutdown()
        drop_database(database, run)
        shutil.rmtree(work, ignore_errors=True)
        out.mkdir(parents=True, exist_ok=True)
        transcript = out / "transcript.md"
        transcript.write_text("\n".join(run.lines) + "\n", encoding="utf-8")
        print(f"\nTranscript: {transcript}")
    return status


def drop_database(database: str, run: Run) -> None:
    """Drops the database this run created, and only that one. The dev database is never named
    here."""
    if not DB_NAME.match(database):
        return
    try:
        psql(
            "postgres",
            f'drop database if exists "{database}" with (force)',
        )
        run.say(f"Dropped {database}.")
    except StepFailed as error:
        run.say(f"Could not drop {database}: {error}")


def _run(
    run: Run,
    services: Services,
    database: str,
    work: Path,
    upstream: ThreadingHTTPServer,
    out: Path,
    serve: bool,
) -> int:
    if not DB_NAME.match(database):
        raise StepFailed(f"Refusing to use the database name {database!r}.")
    for port, what in ((API_PORT, "the API"), (WEB_PORT, "the web app")):
        require_port_free(port, what)
    upstream_port = upstream.server_address[1]
    media = out / "media"
    if out.exists() and not serve:
        shutil.rmtree(out)
    media.mkdir(parents=True, exist_ok=True)
    downloads = work / "downloads"
    files_root = work / "files"
    home = work / "home"
    home.mkdir()
    secrets_file = work / "secrets.json"
    bin_dir = home / ".local" / "bin"
    dsh_home = home / ".dsh"
    launcher = bin_dir / "garden"
    dsh = bin_dir / "dsh"
    # Only the variables below reach the portal, the installer and the CLI. A GARDEN_ variable
    # from the shell or from .env (such as the dev database) is dropped, so this run can never
    # reach the dev stack.
    inherited = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
    cli_env = {
        **inherited,
        "HOME": str(home),
        "PYTHONIOENCODING": "utf-8",
        "GARDEN_API_URL": PORTAL,
        "GARDEN_SECRET_STORE": f"file:{secrets_file}",
        "GARDEN_PYTHON": sys.executable,
    }
    portal_env = {
        **inherited,
        "GARDEN_ENVIRONMENT": "dev",
        "GARDEN_DATABASE_URL": f"{PG_URL}/{database}",
        "GARDEN_FAKE_OIDC": "true",
        "GARDEN_OIDC_ISSUER": f"{OIDC_URL}/default",
        "GARDEN_OIDC_CLIENT_ID": "garden-dev",
        "GARDEN_OIDC_CLIENT_SECRET": "garden-dev-secret",
        "GARDEN_PUBLIC_URL": WEB,
        "GARDEN_FILES_ROOT": str(files_root),
        "GARDEN_AGENT_DOWNLOADS_DIR": str(downloads),
        "GARDEN_AGENT_MODEL_UPSTREAM_URL": f"http://127.0.0.1:{upstream_port}",
        "GARDEN_AGENT_MODEL_MODELS": MODEL,
        "GARDEN_AGENT_MODEL_DEFAULT": MODEL,
    }

    run.heading(
        "0. The stack: a fresh test database, the portal, the web app and a fake model upstream"
    )
    run.say(
        f"The dev Postgres container serves the new database {database}. "
        "The dev database is not used."
    )
    compose = ["docker", "compose", "-f", str(REPO / "dev" / "compose.yaml")]
    created = subprocess.run(
        [
            *compose,
            "exec",
            "-T",
            "postgres",
            "psql",
            "-U",
            "garden",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-c",
            f'create database "{database}"',
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    run.record(f"create database {database}", created.stdout + created.stderr, created.returncode)
    run.check(
        created.returncode == 0, "the fresh test database is created on the dev Postgres container"
    )
    if created.returncode != 0:
        raise StepFailed("The test database could not be created.")
    steps = (("migrate", "migrated", ["app.migrate"]), ("seed", "seeded", ["app.seed"]))
    for name, done, args in steps:
        step = subprocess.run(
            ["uv", "run", "python", "-m", *args],
            cwd=REPO / "backend",
            env=portal_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=600,
        )
        run.record(
            f"backend: python -m {args[0]} (isolated database)",
            step.stdout + step.stderr,
            step.returncode,
        )
        run.check(step.returncode == 0, f"the fresh database is {done}")
        if step.returncode != 0:
            raise StepFailed(f"{name} failed.")

    run.say(f"The fake model upstream answers on 127.0.0.1:{upstream_port}, for the model {MODEL}.")
    wait_for(f"{OIDC_URL}/default/.well-known/openid-configuration")
    run.check(True, f"the dev identity provider answers at {OIDC_URL}")

    services.start(
        ["uv", "run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(API_PORT)],
        cwd=REPO / "backend",
        env=portal_env,
        log=out / "api.log",
    )
    wait_for(f"{PORTAL}/api/health")
    run.check(True, f"the isolated API answers at {PORTAL}")
    services.start(
        ["bun", "run", "dev", "--strictPort", "--port", str(WEB_PORT)],
        cwd=REPO / "frontend",
        env={**inherited, "GARDEN_E2E_API_URL": PORTAL},
        log=out / "web.log",
    )
    wait_for(WEB)
    run.check(True, f"the isolated web app answers at {WEB}")

    run.heading("1. Publish the installer's files into the portal's downloads folder")
    publish = subprocess.run(
        ["just", "agent-publish"],
        cwd=REPO,
        env={**inherited, "GARDEN_AGENT_DOWNLOADS_DIR": str(downloads)},
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    run.record(
        "just agent-publish (GARDEN_AGENT_DOWNLOADS_DIR=<run folder>)",
        publish.stdout[-600:] + publish.stderr,
        publish.returncode,
    )
    run.check(
        publish.returncode == 0,
        "just agent-publish copies the files into the run's downloads folder",
    )
    if publish.returncode != 0:
        raise StepFailed("agent-publish failed.")
    manifest_status, manifest_text = api("GET", "/api/agent/manifest")
    run.check(
        manifest_status == 200, "the portal's manifest lists the published files, with no sign-in"
    )
    artifacts = json.loads(manifest_text)["artifacts"] if manifest_status == 200 else {}

    if serve:
        return serve_until_stopped(run, services, artifacts, out)

    run.heading("2. The installer, run with curl | sh and a fresh HOME")
    run.say(
        "The command is the one the README gives, with `sh` as the shell. The installer runs "
        "with a fresh HOME, reuses this run's Python 3.12 (GARDEN_PYTHON, so no Python "
        "download), and writes the login link to a file "
        "the headless browser approves. Its sign-in is the first login."
    )
    install_env = {
        **cli_env,
        "GARDEN_INSTALL_TEST": "1",
        "GARDEN_LOGIN_LINK_FILE": str(out / "login-link.txt"),
    }
    install_log = out / "install.log"
    installer = start_installer(install_env, install_log)
    deadline = time.monotonic() + 120
    link_file = out / "login-link.txt"
    while not link_file.exists() and time.monotonic() < deadline and installer.poll() is None:
        time.sleep(0.2)
    if not link_file.exists():
        installer.kill()
        raise StepFailed(
            "The installer did not print a login link. See agent/dist/e2e/install.log."
        )
    link = link_file.read_text(encoding="utf-8").strip()
    run.secrets.append(link)
    run.say("The installer wrote the login link, and the headless browser approves it.")
    approval = approve("approve-login.cjs", link, media)
    run.record(
        "node scripts/approve-login.cjs <login link> (headless)",
        approval.stdout + approval.stderr,
        approval.returncode,
    )
    run.check(
        approval.returncode == 0, "the installer's login was approved in the headless browser"
    )
    installer_code = installer.wait(timeout=600)
    install_text = install_log.read_text(encoding="utf-8")
    run.record("sh install.sh (log)", install_text, installer_code)
    run.check(installer_code == 0, "the installer finished with exit 0")
    run.check("Failed:" not in install_text, "the installer printed no failed stage")
    if installer_code != 0:
        raise StepFailed("The installer failed. See the transcript.")

    run.heading("3. What the installer left in the fresh HOME")
    installed_sha = hashlib.sha256((home / ".garden" / "garden.pyz").read_bytes()).hexdigest()
    run.check(
        installed_sha == artifacts.get("garden.pyz", {}).get("sha256"),
        "the installed garden.pyz matches the manifest's SHA-256",
    )
    run.check(
        "Done: Downloading garden.pyz" in install_text
        and "Done: Downloading dsh-bundle.tar.gz" in install_text
        and "Done: Downloading dsh-config.yml" in install_text,
        "the installer checked each download's SHA-256 before it installed it",
    )
    run.check(
        launcher.exists() and os.access(launcher, os.X_OK), "the garden launcher is installed"
    )
    run.check(dsh.exists() and os.access(dsh, os.X_OK), "the dsh launcher is installed")
    run.check(
        (
            home / ".garden" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
        ).exists(),
        "dsh is unpacked from the portal's bundle",
    )
    skill_files = list((home / ".garden" / "skills").rglob("SKILL.md"))
    run.check(bool(skill_files), "the garden skills are installed")
    whoami = subprocess.run(
        [str(launcher), "whoami", "--json"],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    run.record("garden whoami --json", whoami.stdout + whoami.stderr, whoami.returncode)
    run.check(
        whoami.returncode == 0 and bool(json.loads(whoami.stdout).get("name")),
        "the CLI is signed in as a person",
    )
    token = read_token(secrets_file)
    run.secrets.append(token)
    version = subprocess.run(
        [str(dsh), "--version"],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    run.record("dsh --version", version.stdout + version.stderr, version.returncode)
    run.check(
        version.returncode == 0 and "0.2.1-rc.5" in version.stdout,
        "dsh starts from the portal's bundle",
    )

    run.heading("4. garden login from the same HOME, approved in the headless browser")
    login_code, login_out = sign_in_headless(
        [str(launcher), "login", "--device", "e2e-laptop", "--no-open"],
        cli_env,
        media,
        run,
        "garden login",
    )
    run.record("garden login --device e2e-laptop --no-open (log)", login_out, login_code)
    run.check(login_code == 0, "garden login finished with exit 0")
    token = read_token(secrets_file)
    run.secrets.append(token)

    run.heading("5. dsh-config: the patch names the gateway and the default model, and no token")
    config = subprocess.run(
        [str(launcher), "dsh-config"],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    run.record("garden dsh-config", config.stdout + config.stderr, config.returncode)
    run.check(
        config.returncode == 0,
        "garden dsh-config writes dsh's patch from the portal's default model",
    )
    patch_text = (dsh_home / "cordis.patch.yml").read_text(encoding="utf-8")
    run.check(
        f"{PORTAL}{GATEWAY_PATH}" in patch_text,
        "the patch points dsh at the portal's model gateway",
    )
    run.check(
        f'model: "{MODEL}"' in patch_text and f'id: "{MODEL}"' in patch_text,
        "the patch names the default model",
    )
    run.check(
        "serverName: garden" in patch_text and "GARDEN_AGENT_MODEL_TOKEN" in patch_text,
        "the patch names the garden server and the token variable",
    )
    run.check(token not in patch_text, "the CLI login is not in the dsh patch")

    run.heading("6. The MCP server: the tools this person may use")
    mcp = Mcp(launcher, cli_env)
    listed = mcp.request("tools/list")["result"]["tools"]
    names = {tool["name"]: tool for tool in listed}
    run.say(f"{len(listed)} tools are listed for this person.")
    run.check(
        "files_file_upload" in names and "files_file_read" in names, "the file tools are listed"
    )
    run.check(
        "agent_confirmation_decide" not in names,
        "approving a confirmation is not a tool an agent sees",
    )
    hint = names.get(CRITICAL_TOOL, {}).get("annotations", {}).get("destructiveHint")
    run.check(hint is True, f"{CRITICAL_TOOL} is marked destructive (advisory hint)")
    topics = mcp.request(
        "tools/call", {"name": "files_file_read", "arguments": {"sha256": PROBE_SHA}}
    )
    run.record("tools/call files_file_read (an unknown file)", json.dumps(topics, indent=2))
    run.check("error" not in topics, "a read tool answers through MCP")

    run.heading("7. A critical action waits for the person (ADR 0012), then runs once")
    status, body = api(
        "POST",
        f"/api/agent/my-confirmations/{uuid.uuid4()}/decision",
        token=token,
        body={"decision": "approve"},
    )
    run.record(
        "POST /api/agent/my-confirmations/<id>/decision with the CLI login",
        f"{status} {body}",
        status,
    )
    run.check(status == 403, "an agent login cannot decide a confirmation")
    upload_status, upload_text = upload_file(token, "e2e-audit.txt", f"e2e {uuid.uuid4()}".encode())
    run.record("POST /api/files with the CLI login (an agent's upload)", upload_text, upload_status)
    run.check(upload_status == 200, "the agent uploads a file through its CLI login")
    upload_sha = str(json.loads(upload_text).get("sha256", PROBE_SHA))
    delete_id = mcp.start(
        "tools/call", {"name": CRITICAL_TOOL, "arguments": {"sha256": upload_sha}}
    )
    notice = mcp.notice()["params"]["data"]
    run.record("the log notification for the delete call (before any approval)", notice)
    run.check(
        "Confirmation required" in notice,
        "the delete waits for the person, and says confirmation required",
    )
    link_match = LINK.search(notice)
    run.check(link_match is not None, "the notification carries the approval link")
    if link_match is not None:
        link = link_match.group(0)
        run.secrets.append(link)
        approval = approve("approve-confirmation.cjs", link, media)
        run.record(
            "node scripts/approve-confirmation.cjs <approval link> (headless)",
            approval.stdout + approval.stderr,
            approval.returncode,
        )
        run.check(
            approval.returncode == 0, "the person approved the delete in the headless browser"
        )
    deleted = mcp.reply(delete_id)
    run.record(f"tools/call {CRITICAL_TOOL}, after the approval", json.dumps(deleted, indent=2))
    run.check(
        deleted["result"]["isError"] is False,
        "after the approval, the delete runs once and the portal accepts it",
    )
    gone_status, _ = api("GET", f"/api/files/{upload_sha}", token=token)
    run.check(gone_status == 404, "the approved delete removed the file the agent uploaded")
    mcp.close()
    # The audit trail names the agent's upload and the approved delete, both made through the CLI.
    upload_rows = psql(
        database,
        "select count(*) from audit.event where target = 'file:" + upload_sha + "' "
        "and operation = 'files.file.upload' and acting_as_agent",
    )
    run.check(upload_rows == "1", "the agent's upload is in the audit trail, made through an agent")
    delete_rows = psql(
        database,
        "select count(*) from audit.event where target = 'file:" + upload_sha + "' "
        "and operation = 'files.file.delete' and acting_as_agent "
        "and confirmation_id is not null and confirmed_at is not null",
    )
    run.check(
        delete_rows == "1",
        "the approved delete is in the audit trail: made through an agent, named by its approval",
    )

    run.heading("8. dsh, through the portal's model gateway")
    overlay = dsh_overlay(launcher, secrets_file, dsh_home / "cordis.patch.yml", out)
    dsh_status, dsh_text = upload_file(token, "e2e-dsh.txt", f"e2e dsh {uuid.uuid4()}".encode())
    run.record("POST /api/files with the CLI login (the file dsh deletes)", dsh_text, dsh_status)
    run.check(dsh_status == 200, "the agent uploads the file dsh deletes through its CLI login")
    dsh_sha = str(json.loads(dsh_text).get("sha256", PROBE_SHA))
    task = (
        "Use the garden tools. Delete the file with SHA-256 "
        f"{dsh_sha}, then say what the portal answered."
    )
    before_calls = len(FakeUpstream.calls)
    FakeUpstream.critical_sha = dsh_sha
    try:
        dsh_code, dsh_out, dsh_err, dsh_approvals = run_dsh(
            [str(dsh), "--patch", str(overlay), "--profile", "headless", "--json", task],
            cli_env,
            media,
            run,
        )
    finally:
        FakeUpstream.critical_sha = PROBE_SHA
    events = [
        line[:600] for line in dsh_out.splitlines() if "tool" in line or '"type":"final"' in line
    ]
    notes = [line[:600] for line in dsh_err.splitlines() if not line.startswith("warning:")]
    run.record(
        "dsh --patch <overlay> --profile headless --json <task>",
        "\n".join([*events, *notes]),
        dsh_code,
    )
    run.check(dsh_code == 0, "dsh finished with exit 0")
    run.check(
        any(CRITICAL_TOOL in event for event in events),
        "dsh called the delete tool through MCP, with the gateway's model",
    )
    for approval in dsh_approvals:
        run.record(
            "node scripts/approve-confirmation.cjs <approval link> (dsh's call, headless)",
            approval.stdout + approval.stderr,
            approval.returncode,
        )
    run.check(
        len(dsh_approvals) == 1 and dsh_approvals[0].returncode == 0,
        "dsh's delete waited for the person, and was approved in the headless browser",
    )
    run.check(
        REPLY_MARKER in dsh_out,
        "dsh printed the fake model's answer, which reports the tool's result",
    )
    run.check(
        len(FakeUpstream.calls) - before_calls >= 2,
        "the fake upstream served dsh's calls through the gateway",
    )
    # The tool results the upstream got back: the delete ran, so none of them is an error.
    dsh_results = [
        str(message.get("content", ""))
        for call in FakeUpstream.calls[before_calls:]
        for message in call.get("messages") or []
        if message.get("role") == "tool"
    ]
    run.check(
        bool(dsh_results) and not any('"error"' in result for result in dsh_results),
        "dsh's delete returned a result to the model, and the result is not an error",
    )
    gone_dsh, _ = api("GET", f"/api/files/{dsh_sha}", token=token)
    run.check(gone_dsh == 404, "the file dsh deleted is gone: a read answers 404")
    # The trail names the delete: through an agent, named by its approval, and by the CLI login's
    # device and creation time (the API does not show these, so they are read from the table).
    dsh_rows = psql(
        database,
        "select count(*) from audit.event where target = 'file:" + dsh_sha + "' "
        "and operation = 'files.file.delete' and acting_as_agent "
        "and confirmation_id is not null and confirmed_at is not null "
        "and device = 'e2e-laptop' and login_created_at is not null",
    )
    run.check(
        dsh_rows == "1",
        "the audit trail has dsh's delete: through an agent, its approval, and the login's device",
    )

    run.heading("9. The gateway: a chat call, a refused model, usage, and the per-minute limit")
    gateway = GATEWAY_PATH
    models_status, models_text = api("GET", f"{gateway}/models", token=token)
    run.record(f"GET {gateway}/models", models_text, models_status)
    listed_models = json.loads(models_text) if models_status == 200 else {}
    default_model = str(listed_models.get("default", ""))
    run.check(
        models_status == 200 and default_model == MODEL,
        "the gateway names the default model for this person",
    )
    chat_status, chat_text = api(
        "POST", f"{gateway}/chat/completions", token=token, body={"model": MODEL, **PING}
    )
    run.record(f"POST {gateway}/chat/completions (the default model)", chat_text, chat_status)
    chat = json.loads(chat_text) if chat_status == 200 else {}
    run.check(
        chat_status == 200 and "pong" in json.dumps(chat),
        "the gateway answers a chat call through the fake upstream",
    )
    run.check(
        int((chat.get("usage") or {}).get("prompt_tokens", 0)) > 0,
        "the answer carries the upstream's usage",
    )
    tokens_before = _tokens_today(token)
    streamed_status, streamed_text = api(
        "POST",
        f"{gateway}/chat/completions",
        token=token,
        body={"model": MODEL, **PING, "stream": True},
    )
    run.record(
        f"POST {gateway}/chat/completions (stream: true)", streamed_text[:600], streamed_status
    )
    run.check(
        streamed_status == 200 and "pong" in streamed_text and "data: [DONE]" in streamed_text,
        "a streamed chat call is relayed to the end",
    )
    run.check(
        _tokens_today(token) - tokens_before > 0,
        "a streamed call records its tokens, because the gateway asks the upstream for usage",
    )
    refused_status, refused_text = api(
        "POST", f"{gateway}/chat/completions", token=token, body={"model": "not/listed", **PING}
    )
    run.record(
        f"POST {gateway}/chat/completions (a model not on the list)", refused_text, refused_status
    )
    run.check(refused_status == 403, "a model that is not on the list is refused")
    usage_status, usage_text = api("GET", "/api/agent/model/my-usage", token=token)
    run.record("GET /api/agent/model/my-usage", usage_text, usage_status)
    usage = json.loads(usage_text) if usage_status == 200 else {}
    run.check(
        usage_status == 200 and int(usage.get("requests", 0)) >= 3,
        "the person's usage counts today's calls",
    )
    limit = 20
    limited: str | None = None
    for _ in range(limit + 5):
        status, text = api(
            "POST", f"{gateway}/chat/completions", token=token, body={"model": MODEL, **PING}
        )
        if status == 429:
            limited = text
            break
    run.check(
        limited is not None and "minute" in limited,
        "the per-minute limit answers 429 and says when to try again",
    )
    if limited is not None:
        run.record("POST chat/completions over the limit (429)", limited, 429)
    run.heading("10. Sign-out, and the old login refused")
    signed_out = subprocess.run(
        [str(launcher), "logout"],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    run.record("garden logout", signed_out.stdout + signed_out.stderr, signed_out.returncode)
    run.check(signed_out.returncode == 0, "garden logout exits 0")
    status, body = api("GET", "/api/users/me", token=token)
    run.check(
        status == 401, "the old CLI login is refused after sign-out, because the portal ended it"
    )
    whoami_after = subprocess.run(
        [str(launcher), "whoami"],
        env=cli_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    run.record("garden whoami", whoami_after.stdout + whoami_after.stderr, whoami_after.returncode)
    run.check(whoami_after.returncode != 0, "the CLI reports it is not signed in")

    if run.failures:
        run.say("\nFailed checks:\n" + "\n".join(f"- {failure}" for failure in run.failures))
        return 1
    run.say("\nAll checks passed.")
    return 0


def start_installer(env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    """Runs the installer the way the README gives it: curl's output piped to sh."""
    handle = log.open("w", encoding="utf-8")
    return subprocess.Popen(
        ["sh", "-c", f"curl -fsSL {PORTAL}/api/agent/downloads/install.sh | sh"],
        env=env,
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def install_stop_handlers() -> None:
    """Makes Ctrl+C and a SIGTERM end the serve mode through its teardown, not mid-run."""

    def stop(signum: int, _frame: Any) -> None:
        raise Interrupted(signal.Signals(signum).name)

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)


def fetch_sha256(url: str) -> str:
    """The SHA-256 of what the URL serves, streamed so a large bundle is never held in memory."""
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=600) as response:
        while chunk := response.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def windows_instructions() -> str:
    """What to paste into Windows PowerShell, and the steps that follow. Every name here is what
    install.ps1 and the CLI really use."""
    origin = WEB
    api = f"http://localhost:{API_PORT}"
    return f"""
Serve mode is up. Keep this window open. Windows reaches the stack through localhost.
  Web app and installer origin: {origin}
  API, the same portal: {api}
  Dev sign-in provider: {OIDC_URL}

On the Windows PC, open Windows Terminal with PowerShell (5.1 or 7) and paste this one-liner:

  $env:GARDEN_API_URL = '{origin}'; irm {origin}/api/agent/downloads/install.ps1 | iex

What the installer does on that PC:
- Fetches the manifest, then garden, the dsh bundle and the config from the portal.
  It checks each SHA-256 before it installs anything.
- Downloads uv 0.13.1 and Python 3.12.15 from GitHub, so the PC needs internet access.
- Writes to your profile: %USERPROFILE%\\.local\\bin (garden.cmd, dsh.cmd),
  %USERPROFILE%\\.garden and %USERPROFILE%\\.dsh.
  It also writes "Garden Agent" shortcuts to the Start menu and the Desktop.
- Opens your browser at the dev sign-in page. Choose "Dev Admin"
  (dev.admin@fernhill.example, admin). The dev provider has no password.
- dsh needs Node.js. Without it the installer still finishes and says so.

When the installer says it is done, open a NEW Windows Terminal tab, so the PATH change
applies there. Then run:

  garden whoami
  garden logout
  garden login
      (the browser opens again: choose Dev Admin)
  garden tools
  garden dsh-config
      (the installer already wrote the patch; running it again is harmless)
  dsh --profile headless --json "Reply in one short sentence: what did the portal say?"
  garden logout

The dsh prompt goes through the portal's model gateway to the fake upstream.
In serve mode the fake answers in plain text and calls no tool, so nothing is deleted
and nothing waits for approval.

To stop: press Ctrl+C in this window. That stops the processes and drops the test
database, so the Windows sign-in stops working with it.
The files the installer put on the Windows PC stay there; remove them by hand if you
want the PC clean.
"""


def serve_until_stopped(run: Run, services: Services, artifacts: dict[str, Any], out: Path) -> int:
    """Holds the stack up for a hand test from Windows. It returns only when a check fails.
    Otherwise it runs until a stop signal (Interrupted) or a service stops (StepFailed)."""
    for name in WINDOWS_FILES:
        expected = str(artifacts.get(name, {}).get("sha256", ""))
        run.check(bool(expected), f"the manifest lists {name} with its SHA-256")
        try:
            actual = fetch_sha256(f"{WEB}/api/agent/downloads/{name}")
        except (urllib.error.URLError, OSError) as error:
            actual = f"error: {error}"
        run.check(
            bool(expected) and actual == expected,
            f"{name} is served at {WEB} with the manifest's SHA-256",
        )
    if run.failures:
        run.say("\nSome checks failed, so the stack is not held up. Stopping.")
        return 1
    run.say(windows_instructions())
    run.say(f"Logs: {out}. Press Ctrl+C here to stop.")
    while True:
        time.sleep(1)
        for proc in services.procs:
            if proc.poll() is not None:
                raise StepFailed(
                    f"A service stopped on its own (exit {proc.returncode}). See the logs in {out}."
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="The agent end-to-end run, or its serve mode.")
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Hold the stack up for a hand test from Windows until Ctrl+C.",
    )
    sys.exit(main(serve=parser.parse_args().serve))
