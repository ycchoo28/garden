"""Cross-platform local demo lifecycle. Model credentials belong only to the portal."""

from __future__ import annotations

import argparse
import contextlib
import difflib
import getpass
import hashlib
import json
import os
import platform
import secrets
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
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from install_manifest import (
        apply_artifact_plan,
        artifact_plan,
        load_manifest,
        manifest_path,
        owns_artifact,
        record_artifact,
        record_demo_project,
        record_docker_image,
        record_login,
        save_manifest,
        trash_path,
        without_owned_path_entries,
    )
except ModuleNotFoundError:
    try:
        from scripts.install_manifest import (
            apply_artifact_plan,
            artifact_plan,
            load_manifest,
            manifest_path,
            owns_artifact,
            record_artifact,
            record_demo_project,
            record_docker_image,
            record_login,
            save_manifest,
            trash_path,
            without_owned_path_entries,
        )
    except ModuleNotFoundError:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from install_manifest import (
            apply_artifact_plan,
            artifact_plan,
            load_manifest,
            manifest_path,
            owns_artifact,
            record_artifact,
            record_demo_project,
            record_docker_image,
            record_login,
            save_manifest,
            trash_path,
            without_owned_path_entries,
        )

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEYS = tuple(
    "GARDEN_AGENT_MODEL_" + name for name in ("UPSTREAM_URL", "API_KEY", "MODELS", "DEFAULT")
)
FAKE_FLAG = "GARDEN_DEMO_FAKE_MODEL"
# Bootstrap copies that `just demo-uninstall` runs through; kept while a login awaits --drop-login.
RECOVERY_TOOLS = ("just", "just.exe", "uv", "uv.exe")
JUST_DOTENV_PATH = "GARDEN_DEMO_JUST_DOTENV_PATH"
# Compose reads these from the process environment. A docker.cmd bridge that starts Docker in
# WSL does not inherit Windows variables, so WSLENV has to name them for the bridge.
COMPOSE_ENV_KEYS = (
    "GARDEN_DEV_POSTGRES_IMAGE",
    "GARDEN_DEV_POSTGRES_PORT",
    "GARDEN_DEV_OIDC_PORT",
    "AO_SESSION_ID",
)


def wsl_env(existing: str, keys: tuple[str, ...]) -> str:
    """Add keys to a WSLENV value without dropping or repeating existing entries."""
    parts = [part for part in existing.split(":") if part]
    names = {part.split("/", 1)[0] for part in parts}
    return ":".join([*parts, *(key for key in keys if key not in names)])


DOCKER_GUIDANCE = {
    "Linux": (
        "Linux: install Docker Engine and the Compose plugin, then start the daemon and make sure "
        "your user can access it. See https://docs.docker.com/engine/install/ and "
        "https://docs.docker.com/compose/install/linux/."
    ),
    "Darwin": (
        "macOS: install and start Docker Desktop. "
        "See https://docs.docker.com/desktop/setup/install/mac-install/."
    ),
    "Windows": (
        "Windows: native commands need Docker and Compose available on Windows. "
        "A Docker CLI installed only inside WSL is not detected; run the demo inside WSL or "
        "provide a Windows docker.cmd bridge on the current session PATH. Otherwise install and "
        "start Docker Desktop with its WSL 2 backend and Linux containers. "
        "See https://docs.docker.com/desktop/setup/install/windows-install/."
    ),
}


def check_docker() -> None:
    system = platform.system()
    guidance = DOCKER_GUIDANCE.get(
        system, "Install Docker Engine with Docker Compose and start the Docker daemon."
    )
    if not shutil.which("docker"):
        raise RuntimeError(f"Docker CLI was not found. {guidance}")
    for label, command in (
        ("Docker Compose", ["docker", "compose", "version"]),
        ("Docker daemon", ["docker", "info"]),
    ):
        try:
            subprocess.run(
                command_for(command),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            raise RuntimeError(f"{label} is unavailable or not running. {guidance}") from None


def check_demo_port(port: int) -> None:
    with socket.socket() as probe:
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as error:
            raise RuntimeError(f"Demo loopback port {port} is unavailable: {error}") from error


def windows_batch_command(command: list[str]) -> list[str]:
    return ["cmd.exe", "/d", "/c", "call " + subprocess.list2cmdline(command)]


def command_for(args, path=None):
    executable = (
        shutil.which(str(args[0]), path=path) if path is not None else shutil.which(str(args[0]))
    ) or str(args[0])
    command = [executable, *map(str, args[1:])]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        return windows_batch_command(command)
    return command


# Every wait in cleanup is bounded. Seconds.
LOGOUT_TIMEOUT = 30
DOCKER_TIMEOUT = 60
IMAGE_TIMEOUT = 120
COMPOSE_TIMEOUT = 300
RUNNER_STOP_TIMEOUT = 60
RUNNER_KILL_TIMEOUT = 20
# How long the runner waits for its services to stop before it ends them, shared by all of them.
CHILD_STOP_TIMEOUT = 15
# After a kill, how long to poll until the runner, its children and its ports are gone.
RUNNER_GONE_TIMEOUT = 30


def run_bounded(command, *, timeout: float, env=None, cwd=None) -> subprocess.CompletedProcess:
    """Run a command with a hard time limit and output captured in temporary files.

    Output goes to files rather than pipes, so a grandchild that inherits a pipe cannot keep
    the wait open after the limit. subprocess.TimeoutExpired propagates to the caller.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            timeout=timeout,
            check=False,
            env=env,
            cwd=cwd,
        )
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(
            command,
            result.returncode,
            out.read().decode("utf-8", errors="replace"),
            err.read().decode("utf-8", errors="replace"),
        )


def load_env(path: Path) -> dict[str, str]:
    # Use the same dotenv parser as Just, including quoted tokens and comments.
    from dotenv import dotenv_values

    return {
        key: value
        for key, value in dotenv_values(path, interpolate=False).items()
        if value is not None
    }


def save_env(path: Path, values: dict[str, str]) -> None:
    from dotenv import set_key

    # Restrict access before writing any key; set_key replaces atomically.
    path.chmod(0o600)
    for key, value in values.items():
        if "\n" in value or "\r" in value:
            raise ValueError("Model settings must each fit on one line")
        set_key(path, key, value, quote_mode="always")
    path.chmod(0o600)


def request_json(url: str, key: str = "", body: dict | None = None, timeout: float = 15):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(
        url, headers=headers, data=None if body is None else json.dumps(body).encode()
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


PROVIDER_PRESETS = (
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("OpenAI", "https://api.openai.com/v1"),
    ("Other OpenAI-compatible URL", ""),
    ("Local fake model (demo only)", "fake"),
)
OPENROUTER_PRIVACY_URL = "https://openrouter.ai/settings/privacy"
# Small enough for every provider's minimum. A reasoning model may return no text at this size,
# so the check only needs a reply with choices.
CHECK_MAX_TOKENS = 16
MODEL_PAGE = 20


@dataclass(frozen=True)
class UpstreamProblem:
    """Why the upstream check failed. `step` names the setup step to return to."""

    step: str  # "url", "key", "model", or "retry" (same settings, try again)
    reason: str
    hint: str = ""
    suggestions: tuple[str, ...] = ()

    def text(self) -> str:
        lines = [f"Reason: {self.reason}"]
        if self.suggestions:
            lines.append("Closest model ids: " + ", ".join(self.suggestions))
        if self.hint:
            lines.append(f"Hint: {self.hint}")
        return "\n".join(lines)


def _redact(text: object, key: str) -> str:
    cleaned = " ".join(str(text).split())
    if key:
        cleaned = cleaned.replace(key, "[key hidden]")
    return cleaned[:300]


def _upstream_message(error: urllib.error.HTTPError, key: str) -> str:
    try:
        raw = error.read().decode("utf-8", "replace")
    except OSError:
        raw = ""
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    message: object = raw
    if isinstance(body, dict):
        found = body.get("error", body)
        if isinstance(found, dict):
            found = found.get("message") or found.get("code")
        message = found or raw
    return _redact(message, key)


def hint_for(status: int | None, message: str = "", *, listing: bool = False) -> tuple[str, str]:
    """Map an upstream HTTP status to the setup step to return to, and a plain hint."""
    lowered = message.lower()
    if status == 401:
        return "key", "The key was rejected. Enter it again; it is not saved yet."
    if status == 402:
        return (
            "model",
            "No credits left for this model. Add credits, or pick a model that needs none.",
        )
    if status == 403 and "agentic" in lowered:
        return (
            "model",
            "This model only works from agentic harnesses. Pick another model.",
        )
    if status == 403:
        return (
            "model",
            "The key may not use this model. Check the key's permissions or pick another model.",
        )
    if status == 404 and listing:
        return (
            "url",
            "This URL does not answer like an OpenAI-compatible base URL, such as https://openrouter.ai/api/v1.",
        )
    if status == 404 and "data policy" in lowered:
        return "model", (
            "OpenRouter's privacy settings block this model. Free models need data-policy "
            "consent at "
            f"{OPENROUTER_PRIVACY_URL}, or pick a model that is not free."
        )
    if status == 404:
        return "model", "The model or its endpoint was not found. Check the model id."
    if status == 429:
        if listing:
            return "retry", "Rate limited while listing models. Wait a moment and retry."
        return (
            "model",
            "Rate limited. Free models are shared and limited. Wait, or pick a non-free model.",
        )
    if status == 400:
        return "model", "The upstream rejected the request for this model. Pick another model."
    if status is not None and status >= 500:
        return "retry", "The upstream is failing. Wait a few minutes, then retry."
    return (
        "url" if listing else "retry"
    ), "Unexpected answer from the upstream. Retry, or change the URL."


def _http_problem(error: urllib.error.HTTPError, key: str, *, listing: bool) -> UpstreamProblem:
    message = _upstream_message(error, key)
    step, hint = hint_for(error.code, message, listing=listing)
    return UpstreamProblem(step, f"HTTP {error.code}: {message or error.reason}", hint)


def _network_problem(error: Exception, key: str, step: str) -> UpstreamProblem:
    cause = getattr(error, "reason", error)
    return UpstreamProblem(
        step,
        f"could not reach the upstream ({_redact(cause, key)})",
        "Check the address and your network connection.",
    )


def list_upstream_models(url: str, key: str) -> tuple[list[str], UpstreamProblem | None]:
    try:
        listed = request_json(url.rstrip("/") + "/models", key)
    except urllib.error.HTTPError as error:
        return [], _http_problem(error, key, listing=True)
    except (OSError, ValueError) as error:
        return [], _network_problem(error, key, "url")
    rows = listed.get("data") if isinstance(listed, dict) else None
    if not isinstance(rows, list):
        return [], UpstreamProblem(
            "url",
            "the URL did not return a model list",
            "Check that the URL is an OpenAI-compatible base URL, such as https://openrouter.ai/api/v1.",
        )
    ids = sorted(
        {row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
    )
    return ids, None


def check_chat(url: str, key: str, model: str) -> UpstreamProblem | None:
    """Ask the default model one question. Any reply with choices counts as callable."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply: ready"}],
        "max_tokens": CHECK_MAX_TOKENS,
    }
    try:
        reply = request_json(url.rstrip("/") + "/chat/completions", key, body, timeout=60)
    except urllib.error.HTTPError as error:
        return _http_problem(error, key, listing=False)
    except (OSError, ValueError) as error:
        return _network_problem(error, key, "retry")
    choices = reply.get("choices") if isinstance(reply, dict) else None
    if not isinstance(choices, list) or not choices:
        return UpstreamProblem(
            "model",
            "the model answered without any choices",
            "Pick another model; this one did not produce a reply.",
        )
    return None


def check_settings(env: dict[str, str]) -> UpstreamProblem | None:
    """Return None when the saved upstream settings are usable, else why they are not."""
    url, key, models, default = (env.get(name, "").strip() for name in MODEL_KEYS)
    allowed = [name.strip() for name in models.split(",") if name.strip()]
    if not url.startswith(("http://", "https://")):
        return UpstreamProblem("url", "the base URL must start with http:// or https://")
    if not key:
        return UpstreamProblem("key", "no key is saved")
    if not allowed:
        return UpstreamProblem("model", "no allowed model is set")
    if default not in allowed:
        return UpstreamProblem("model", "the default model is not in the allowed list")
    ids, problem = list_upstream_models(url, key)
    if problem:
        return problem
    missing = [name for name in allowed if name not in ids]
    if missing:
        return UpstreamProblem(
            "model",
            f"not in this upstream's model list: {', '.join(missing)}",
            "Pick a model from the list, or check the id.",
            tuple(suggest_models(missing[0], ids)),
        )
    # Models can be listed while the token cannot invoke them. Check the selected default too.
    return check_chat(url, key, default)


def validate_model(env: dict[str, str]) -> bool:
    return check_settings(env) is None


def mask_key(key: str) -> str:
    return "****" + key[-4:] if len(key) > 8 else "****"


def suggest_models(token: str, ids: list[str]) -> list[str]:
    return difflib.get_close_matches(token, ids, n=5, cutoff=0.5)


def filter_models(ids: list[str], text: str) -> list[str]:
    needle = text.strip().lower()
    return [name for name in ids if needle in name.lower()]


def read_selection(
    tokens: list[str], ids: list[str], shown: list[str]
) -> tuple[list[str], dict[str, list[str]]]:
    """Resolve numbers (from the last list shown) and exact ids. Unknown tokens come back with
    their closest ids, so they can be flagged instead of silently dropped."""
    chosen: list[str] = []
    unknown: dict[str, list[str]] = {}
    for token in tokens:
        if token.isdigit() and 1 <= int(token) <= len(shown):
            name = shown[int(token) - 1]
        elif token in ids:
            name = token
        else:
            unknown[token] = suggest_models(token, ids)
            continue
        if name not in chosen:
            chosen.append(name)
    return chosen, unknown


def choose_provider(ask, say) -> tuple[str, str] | None:
    """Return (provider label, base URL), or None for the local fake model."""
    say("Choose the model provider:")
    for number, (label, url) in enumerate(PROVIDER_PRESETS, start=1):
        say(f"  {number}. {label}" + (f"  {url}" if url and url != "fake" else ""))
    while True:
        answer = ask(f"Provider [1-{len(PROVIDER_PRESETS)}, default 1]: ").strip() or "1"
        if answer == "fake":
            return None
        if not answer.isdigit() or not 1 <= int(answer) <= len(PROVIDER_PRESETS):
            say(f"Enter a number from 1 to {len(PROVIDER_PRESETS)}.")
            continue
        label, url = PROVIDER_PRESETS[int(answer) - 1]
        if url == "fake":
            return None
        if url:
            return label, url
        typed = ask("OpenAI-compatible base URL, or 'fake' for the local model: ").strip()
        if typed == "fake":
            return None
        if typed.startswith(("http://", "https://")):
            return label, typed.rstrip("/")
        say("The URL must start with http:// or https://.")


def choose_models(ids: list[str], ask, say) -> tuple[list[str], str]:
    """Pick allowed models by filter, number or exact id, then the default by number."""
    say(
        f"The upstream lists {len(ids)} models. Type part of a name to filter, "
        "or numbers and exact ids to pick."
    )
    shown: list[str] = []
    while True:
        text = ask("Filter, numbers or ids (blank lists the first 20): ").strip()
        if not text:
            shown = ids[:MODEL_PAGE]
        else:
            tokens = [token.strip() for token in text.split(",") if token.strip()]
            selecting = "," in text or all(token.isdigit() or token in ids for token in tokens)
            if selecting:
                chosen, unknown = read_selection(tokens, ids, shown)
                for token, close in unknown.items():
                    detail = f" Closest: {', '.join(close)}" if close else " No close match."
                    say(f"Not in the list: {token}.{detail}")
                if not unknown and chosen:
                    break
                continue
            matches = filter_models(ids, text)
            if not matches:
                close = suggest_models(text, ids)
                say(
                    f"No model id contains '{text}'."
                    + (f" Closest: {', '.join(close)}" if close else "")
                )
                continue
            shown = matches[:MODEL_PAGE]
            if len(matches) > MODEL_PAGE:
                say(
                    f"{len(matches)} models match; showing the first {MODEL_PAGE}. "
                    "Narrow the filter to see the rest."
                )
        for number, name in enumerate(shown, start=1):
            say(f"  {number:>3}. {name}")
    say("Allowed models:")
    for number, name in enumerate(chosen, start=1):
        say(f"  {number}. {name}")
    while True:
        answer = ask(f"Default model [1-{len(chosen)}, default 1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(chosen):
            return chosen, chosen[int(answer) - 1]
        if answer in chosen:
            return chosen, answer
        say(f"Enter a number from 1 to {len(chosen)}, or one of the ids above.")


def interactive_model_setup(*, ask, secret, say) -> dict[str, str] | None:
    """Ask for provider, key and models, and check them. A failure returns to the step it names.

    Returns the four MODEL_KEYS values, or None for the local fake model.
    """
    step = "url"
    provider = url = key = ""
    ids: list[str] = []
    models: list[str] = []
    default = ""
    while True:
        if step == "url":
            choice = choose_provider(ask, say)
            if choice is None:
                return None
            provider, url = choice
            step = "key"
        elif step == "key":
            key = secret("API key or token (blank to change the URL): ").strip()
            if not key:
                step = "url"
                continue
            ids, problem = list_upstream_models(url, key)
            if problem:
                say(problem.text())
                step = problem.step if problem.step in {"url", "key"} else "key"
                continue
            say(f"Connected. The upstream lists {len(ids)} models.")
            step = "model"
        elif step == "model":
            models, default = choose_models(ids, ask, say)
            step = "check"
        elif step == "check":
            problem = check_chat(url, key, default)
            if problem is not None:
                say(problem.text())
                if problem.step == "retry":
                    again = ask("Press Enter to retry, or type m to pick another model: ")
                    step = "model" if again.strip().lower() == "m" else "check"
                else:
                    step = problem.step
                continue
            say("Summary:")
            say(f"  Provider: {provider}")
            say(f"  URL: {url}")
            say(f"  Models: {', '.join(models)}")
            say(f"  Default: {default}")
            say(f"  Key: {mask_key(key)}")
            answer = ask(
                "Save these settings to the portal .env? [Y/n, or url, key, model to change one]: "
            )
            answer = answer.strip().lower()
            if answer in {"", "y", "yes"}:
                return {
                    MODEL_KEYS[0]: url,
                    MODEL_KEYS[1]: key,
                    MODEL_KEYS[2]: ",".join(models),
                    MODEL_KEYS[3]: default,
                }
            if answer in {"url", "key", "model"}:
                step = answer
                continue
            raise RuntimeError("Model setup cancelled. Nothing was saved.")


def demo_project_name(root: Path, home: Path | None = None) -> str:
    scope = f"{root.resolve()}\0{(home or Path.home()).resolve()}"
    digest = hashlib.sha256(scope.encode()).hexdigest()[:12]
    return f"garden-demo-{digest}"


def demo_image_reference(root: Path, home: Path | None = None) -> str:
    scope = f"{root.resolve()}\0{(home or Path.home()).resolve()}"
    return f"garden-demo-postgres:{hashlib.sha256(scope.encode()).hexdigest()[:12]}"


class FakeModel(BaseHTTPRequestHandler):
    """A local, deterministic demo model, with both JSON and streaming replies."""

    def do_GET(self):
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self.send_json({"object": "list", "data": [{"id": "garden-demo", "object": "model"}]})

    def send_json(self, value):
        data = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        base = {
            "id": "chatcmpl-demo",
            "created": int(time.time()),
            "model": "garden-demo",
        }
        message = {
            "role": "assistant",
            "content": "Garden demo is ready. This is a local fake model reply.",
        }
        usage = {"prompt_tokens": 8, "completion_tokens": 16, "total_tokens": 24}
        if not body.get("stream"):
            self.send_json(
                {
                    **base,
                    "object": "chat.completion",
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                    "usage": usage,
                }
            )
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for value in (
            {
                **base,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": message, "finish_reason": None}],
            },
            {
                **base,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            },
            {**base, "object": "chat.completion.chunk", "choices": [], "usage": usage},
        ):
            self.wfile.write(f"data: {json.dumps(value)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_args):
        pass


class Demo:
    def __init__(self, args):
        self.args = args
        self.env_file = Path(args.env_file).resolve()
        if not self.env_file.exists():
            shutil.copyfile(ROOT / ".env.example", self.env_file)
        self.env = {**os.environ, **load_env(self.env_file)}
        # Just exports its dotenv values to recipe processes. Only values that differ from
        # that file are explicit shell overrides and should take precedence over --env-file.
        dotenv_path = os.environ.get(JUST_DOTENV_PATH)
        just_values = load_env(Path(dotenv_path)) if dotenv_path else {}
        self.env.update(
            {
                key: value
                for key, value in os.environ.items()
                if key.startswith("GARDEN_") and (not dotenv_path or just_values.get(key) != value)
            }
        )
        self.env.pop(JUST_DOTENV_PATH, None)
        if args.web_only:
            for key in MODEL_KEYS:
                self.env[key] = ""
            self.env[FAKE_FLAG] = "0"
        self.procs = []
        self.servers = []
        self.control_server = None
        self.control_thread = None
        self.work = Path(tempfile.mkdtemp(prefix="garden-demo-"))
        self.api_port = int(self.env.get("GARDEN_DEMO_API_PORT", "55000"))
        self.web_port = int(self.env.get("GARDEN_DEMO_WEB_PORT", "55173"))
        self.api = f"http://127.0.0.1:{self.api_port}"
        self.web = f"http://localhost:{self.web_port}"
        self.manifest_file = manifest_path()
        self.project = demo_project_name(ROOT, Path.home())
        self.postgres_image = self.env.get("GARDEN_DEV_POSTGRES_IMAGE") or demo_image_reference(
            ROOT, Path.home()
        )
        self.env["GARDEN_DEV_POSTGRES_IMAGE"] = self.postgres_image
        self.env["GARDEN_PUBLIC_URL"] = self.web
        self.compose_postgres_port = int(self.env.get("GARDEN_DEMO_POSTGRES_PORT", "55433"))
        self.compose_oidc_port = int(self.env.get("GARDEN_DEMO_OIDC_PORT", "55082"))
        self.env["GARDEN_DEV_POSTGRES_PORT"] = str(self.compose_postgres_port)
        self.env["GARDEN_DEV_OIDC_PORT"] = str(self.compose_oidc_port)
        example = load_env(ROOT / ".env.example")
        if self.env.get("GARDEN_DATABASE_URL") == example.get("GARDEN_DATABASE_URL"):
            self.env["GARDEN_DATABASE_URL"] = self.env["GARDEN_DATABASE_URL"].replace(
                ":55432/", f":{self.compose_postgres_port}/", 1
            )
        if self.env.get("GARDEN_OIDC_ISSUER") == example.get("GARDEN_OIDC_ISSUER"):
            self.env["GARDEN_OIDC_ISSUER"] = self.env["GARDEN_OIDC_ISSUER"].replace(
                ":55081/", f":{self.compose_oidc_port}/", 1
            )
        self.env["COMPOSE_PROJECT_NAME"] = self.project
        self.env.pop("VIRTUAL_ENV", None)
        self.stop_event = threading.Event()
        self.control_server = None
        self.control_thread = None

    def start_control(self):
        token = secrets.token_urlsafe(24)
        stop_event = self.stop_event

        class Handler(BaseHTTPRequestHandler):
            def do_POST(handler_self):
                if handler_self.path != "/stop/" + token:
                    handler_self.send_error(404)
                    return
                stop_event.set()
                handler_self.send_response(202)
                handler_self.end_headers()

            def log_message(self, *_args):
                pass

        self.control_server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.control_thread = threading.Thread(
            target=self.control_server.serve_forever, daemon=True
        )
        self.control_thread.start()
        data = load_manifest(self.manifest_file)
        record_demo_project(
            data,
            ROOT,
            self.project,
            image=self.postgres_image,
            active=True,
            control_url=f"http://127.0.0.1:{self.control_server.server_port}/stop/{token}",
            # The control port answers only while this process lives, so the PID is safe to use
            # as an escalation target while that port still answers.
            pid=os.getpid(),
            # Cleanup checks these after a stop, so it can tell when the services have gone.
            api_port=self.api_port,
            web_port=self.web_port,
            env_file=str(self.env_file),
        )
        save_manifest(self.manifest_file, data)

    def check_stop(self):
        if self.stop_event.is_set():
            raise KeyboardInterrupt

    def run(self, args, cwd=ROOT, env=None, **kwargs):
        # Resolve .cmd commands explicitly on Windows (npm, bun, and installed launchers).
        result = subprocess.run(
            command_for(args, path=(env or self.env).get("PATH")),
            cwd=cwd,
            env=env or self.env,
            check=True,
            **kwargs,
        )
        self.check_stop()
        return result

    def start(self, args, cwd=ROOT, env=None):
        effective = env or self.env
        process_options = (
            {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
            if os.name == "nt"
            else {"start_new_session": True}
        )
        proc = subprocess.Popen(
            command_for(args, path=effective.get("PATH")),
            cwd=cwd,
            env=effective,
            **process_options,
        )
        self.procs.append(proc)
        return proc

    def healthy(self, url):
        for _ in range(120):
            self.check_processes()
            try:
                with urllib.request.urlopen(url, timeout=2) as response:
                    if response.status == 200:
                        return
            except OSError:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"Timed out waiting for {url}")

    def check_processes(self):
        if any(proc.poll() is not None for proc in self.procs):
            raise RuntimeError("A demo service stopped. See its output above.")

    def setup(self):
        for port in (
            self.api_port,
            self.web_port,
            self.compose_postgres_port,
            self.compose_oidc_port,
        ):
            check_demo_port(port)
        try:
            for folder in ("backend", "agent"):
                venv_python = (
                    ROOT
                    / folder
                    / ".venv"
                    / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                )
                if not venv_python.exists():
                    self.run(["uv", "sync", "--frozen"], ROOT / folder)
            frontend_modules = ROOT / "frontend/node_modules"
            if not frontend_modules.exists():
                command = ["bun", "install", "--frozen-lockfile"]
                if os.name == "nt":
                    # Bun 1.4.3's default Windows link backend produced InvalidBinContent shims.
                    command.append("--backend=copyfile")
                self.run(command, ROOT / "frontend")
        finally:
            # Record what this run created even when an install failed part way.
            record_created_dependencies(self.manifest_file)

    def fake_model(self):
        port = int(self.env.get("GARDEN_DEMO_MODEL_PORT", "55085"))
        server = ThreadingHTTPServer(("127.0.0.1", port), FakeModel)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        self.env.update(
            dict(
                zip(
                    MODEL_KEYS,
                    (
                        f"http://127.0.0.1:{port}/v1",
                        "local-demo-only",
                        "garden-demo",
                        "garden-demo",
                    ),
                    strict=True,
                )
            )
        )
        self.env[MODEL_KEYS[0]] = f"http://127.0.0.1:{server.server_port}/v1"
        self.env[FAKE_FLAG] = "1"

    def use_fake_model(self):
        saved_keys = {
            key: value
            for key, value in load_env(self.env_file).items()
            if key in (MODEL_KEYS[0], MODEL_KEYS[1]) and value.strip()
        }
        self.fake_model()
        if not saved_keys:
            save_env(self.env_file, {FAKE_FLAG: "1", MODEL_KEYS[3]: "garden-demo"})

    def configure_model(self):
        problem = check_settings(self.env)
        valid = problem is None
        fake = self.args.fake_model or (self.env.get(FAKE_FLAG) == "1" and not valid)
        if fake:
            self.use_fake_model()
            return
        if valid:
            print("Reusing validated model upstream.", flush=True)
        else:
            if self.args.non_interactive or not sys.stdin.isatty():
                raise RuntimeError(
                    f"Model upstream is missing or unusable: {problem.reason}. Supply the four "
                    "GARDEN_AGENT_MODEL_* settings or use --fake-model --non-interactive."
                )
            print("Model upstream is missing or unusable. The key stays only in the portal .env.")
            print(problem.text())
            chosen = interactive_model_setup(ask=input, secret=getpass.getpass, say=print)
            if chosen is None:
                self.use_fake_model()
                return
            self.env.update(chosen)
            self.env[FAKE_FLAG] = "0"
        save_env(
            self.env_file,
            {key: self.env.get(key, "0") for key in (*MODEL_KEYS, FAKE_FLAG)},
        )

    def publish(self):
        self.run(["uv", "run", "python", "scripts/build_zipapp.py"], ROOT / "agent")
        # npm optional native packages must match the client host, independent of the publisher.
        import platform

        arch = "arm64" if platform.machine().lower() in ("arm64", "aarch64") else "x64"
        host = (
            {"Linux": "linux", "Darwin": "darwin", "Windows": "windows"}[platform.system()]
            + "-"
            + arch
        )
        for target in dict.fromkeys(
            (host if host != "windows-x64" else "linux-x64", "windows-x64")
        ):
            filename = (
                "dsh-bundle-windows-x64.tar.gz" if target == "windows-x64" else "dsh-bundle.tar.gz"
            )
            bundle = ROOT / "agent/dist" / filename
            marker = bundle.with_suffix(".target")
            lock = max(
                (ROOT / "agent/install/dsh" / name).stat().st_mtime
                for name in ("package.json", "package-lock.json")
            )
            if (
                not bundle.exists()
                or bundle.stat().st_mtime < lock
                or not marker.exists()
                or marker.read_text() != target
            ):
                print(
                    f"Installing dsh dependencies for {target} (npm ci). This can take 10-15 "
                    "minutes on first run; npm prints nothing until it finishes. Please wait.",
                    flush=True,
                )
                self.run(
                    [
                        "uv",
                        "run",
                        "python",
                        "scripts/build_dsh_bundle.py",
                        "--target",
                        target,
                    ],
                    ROOT / "agent",
                )
                marker.write_text(target)
        destination = Path(
            self.env.get("GARDEN_AGENT_DOWNLOADS_DIR", str(ROOT / "backend/var/agent-downloads"))
        ).resolve()
        destination_existed = destination.exists()
        destination.mkdir(parents=True, exist_ok=True)
        self.env["GARDEN_AGENT_DOWNLOADS_DIR"] = str(destination)
        manifest = load_manifest(self.manifest_file)
        if not destination_existed:
            record_artifact(
                manifest,
                destination,
                owner="demo-publish",
                kind="tree",
                root=destination.parent,
            )
        for name in (
            "garden.pyz",
            "dsh-bundle.tar.gz",
            "dsh-bundle-windows-x64.tar.gz",
        ):
            # Windows serves its host bundle under its own name.
            source = ROOT / "agent/dist" / name
            if source.exists():
                target = destination / name
                created = not target.exists()
                shutil.copy2(source, target)
                if destination_existed and (created or owns_artifact(manifest, target)):
                    record_artifact(
                        manifest,
                        target,
                        owner="demo-publish",
                        kind="file",
                        root=destination,
                    )
        for name in ("install.sh", "install.ps1"):
            target = destination / name
            created = not target.exists()
            shutil.copy2(ROOT / "agent/install" / name, target)
            if destination_existed and (created or owns_artifact(manifest, target)):
                record_artifact(
                    manifest,
                    target,
                    owner="demo-publish",
                    kind="file",
                    root=destination,
                )
        config_target = destination / "dsh-config.yml"
        config_created = not config_target.exists()
        shutil.copy2(ROOT / "agent/install/dsh/cordis.patch.yml", config_target)
        if destination_existed and (config_created or owns_artifact(manifest, config_target)):
            record_artifact(
                manifest,
                config_target,
                owner="demo-publish",
                kind="file",
                root=destination,
            )
        commit = self.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        commit_file = destination / "build-commit"
        commit_created = not commit_file.exists()
        commit_file.write_text(commit)
        if destination_existed and (commit_created or owns_artifact(manifest, commit_file)):
            record_artifact(
                manifest,
                commit_file,
                owner="demo-publish",
                kind="file",
                root=destination,
            )
        save_manifest(self.manifest_file, manifest)

    def stack(self):
        # Do not execute shell-only Just recipes: this path also runs natively on Windows.
        postgres_image = self.env.get("GARDEN_DEV_POSTGRES_IMAGE") or self.postgres_image
        manifest = load_manifest(self.manifest_file)
        existing = subprocess.run(
            command_for(["docker", "image", "inspect", "--format", "{{.Id}}", postgres_image]),
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
        )
        if existing.returncode == 0 and not any(
            image.get("reference") == postgres_image and image.get("id") == existing.stdout.strip()
            for image in manifest["docker_images"]
        ):
            raise RuntimeError(f"Refusing to replace the unrecorded Docker image {postgres_image}")
        pins = dict(
            line.split("=", 1)
            for line in (ROOT / "infra/postgres/versions.env").read_text().splitlines()
            if line and not line.startswith("#")
        )
        self.run(
            [
                "docker",
                "build",
                "-f",
                "infra/postgres/Dockerfile",
                "--build-arg",
                "POSTGRES_BASE_IMAGE=" + pins["POSTGRES_BASE_IMAGE"],
                "--build-arg",
                "PGVECTOR_APT_VERSION=" + pins["PGVECTOR_APT_VERSION"],
                "-t",
                postgres_image,
                "infra/postgres",
            ]
        )
        self.env["GARDEN_DEV_POSTGRES_IMAGE"] = postgres_image
        image = self.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", postgres_image],
            capture_output=True,
            text=True,
        ).stdout.strip()
        record_docker_image(manifest, postgres_image, image)
        record_demo_project(
            manifest,
            ROOT,
            self.project,
            image=postgres_image,
            active=True,
            env_file=str(self.env_file),
        )
        save_manifest(self.manifest_file, manifest)
        if os.name == "nt":
            self.env["WSLENV"] = wsl_env(self.env.get("WSLENV", ""), COMPOSE_ENV_KEYS)
        self.run(
            [
                "docker",
                "compose",
                "--project-name",
                self.project,
                "-f",
                "dev/compose.yaml",
                "up",
                "-d",
                "--wait",
            ]
        )
        self.run(["uv", "run", "python", "-m", "app.migrate"], ROOT / "backend")
        real = self.env.get("GARDEN_EMBEDDING_URL", "http://127.0.0.1:55083")
        try:
            with urllib.request.urlopen(real + "/health", timeout=3):
                self.env["GARDEN_EMBEDDING_URL"] = real
        except OSError:
            port = int(self.env.get("GARDEN_DEMO_EMBEDDING_PORT", "55084"))
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
            self.start(
                [
                    "uv",
                    "run",
                    "python",
                    "-m",
                    "tests.fake_embedding_server",
                    "--port",
                    str(port),
                ],
                ROOT / "backend",
            )
            self.env["GARDEN_EMBEDDING_URL"] = f"http://127.0.0.1:{port}"
            self.healthy(self.env["GARDEN_EMBEDDING_URL"] + "/health")
        self.run(["uv", "run", "python", "-m", "app.demo_seed"], ROOT / "backend")
        api_command = [
            "uv",
            "run",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.api_port),
        ]
        if os.name == "nt":
            # Uvicorn selects its own loop, independent of the application policy.
            api_command += ["--loop", "asyncio:SelectorEventLoop"]
        self.start(api_command, ROOT / "backend")
        self.start(["uv", "run", "python", "-m", "app.worker"], ROOT / "backend")
        # Vite and the CLI never inherit the model upstream key.
        client_env = self.client_env()
        client_env["GARDEN_E2E_API_URL"] = self.api
        self.start(
            ["bun", "run", "dev", "--strictPort", "--port", str(self.web_port)],
            ROOT / "frontend",
            client_env,
        )
        self.healthy(self.api + "/api/health")
        self.healthy(self.web)
        print(f"Open {self.web} and choose any seeded persona.", flush=True)

    def client_env(self):
        return {
            key: value
            for key, value in self.env.items()
            if not key.startswith("GARDEN_AGENT_MODEL_")
        }

    def install(self):
        env = self.client_env()
        env["GARDEN_API_URL"] = self.api
        env["GARDEN_LOGIN_LINK_FILE"] = str(self.work / "login-link")
        unattended = self.args.non_interactive
        if unattended and not self.args.persona:
            raise RuntimeError("--non-interactive requires --persona (for example 'Dev Admin')")
        installer = self.work / ("install.ps1" if os.name == "nt" else "install.sh")
        with urllib.request.urlopen(
            self.api + "/api/agent/downloads/" + installer.name
        ) as response:
            installer.write_bytes(response.read())
        command = (
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(installer),
            ]
            if os.name == "nt"
            else ["bash", str(installer)]
        )
        process = self.start(command, env=env)
        deadline = time.monotonic() + 600
        link = ""
        while not link:
            if (self.work / "login-link").exists():
                link = (self.work / "login-link").read_text().strip()
                if link:
                    break
            if process.poll() is not None:
                raise RuntimeError("Installer failed before sign-in")
            if time.monotonic() > deadline:
                raise RuntimeError("Installer timed out before sign-in")
            time.sleep(0.2)
        if self.args.persona:
            names = {
                "Dev Admin": "dev.admin",
                "Marcus Lee": "marcus.lee",
                "Priya Nair": "priya.nair",
                "Lena Fischer": "lena.fischer",
            }
            # Follow OIDC and the real CLI callback for the chosen seeded person.
            self.run(
                ["bun", "x", "playwright", "install", "chromium"],
                ROOT / "frontend",
                env,
            )
            env["GARDEN_E2E_OIDC_URL"] = self.env["GARDEN_OIDC_ISSUER"].rsplit("/", 1)[0]
            self.run(
                [
                    "node",
                    "agent/scripts/approve-login.cjs",
                    link,
                    str(self.work),
                    self.args.persona,
                    names[self.args.persona] + "@fernhill.example",
                ],
                env=env,
            )
        else:
            import webbrowser

            print(
                "Choose your seeded persona in the browser to approve CLI sign-in.",
                flush=True,
            )
            webbrowser.open(link)
        process.wait(timeout=300)
        self.procs.remove(process)
        if process.returncode:
            raise RuntimeError("Installer failed")
        bin_dir = Path(env.get("GARDEN_BIN_DIR", str(Path.home() / ".local/bin")))
        manifest = load_manifest(self.manifest_file)
        record_login(
            manifest,
            self.api,
            str(bin_dir / ("garden.cmd" if os.name == "nt" else "garden")),
            env.get("GARDEN_HOME", str(Path.home() / ".garden")),
        )
        save_manifest(self.manifest_file, manifest)
        garden = bin_dir / ("garden.cmd" if os.name == "nt" else "garden")
        dsh = bin_dir / ("dsh.cmd" if os.name == "nt" else "dsh")
        # The Windows installer persists its Node path for subsequent processes.
        if os.name == "nt":
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                env["PATH"] = winreg.QueryValueEx(key, "Path")[0] + ";" + env["PATH"]
        for args in (
            [garden, "whoami"],
            [garden, "dsh-config"],
            [garden, "tools"],
            [dsh, "--version"],
            [
                dsh,
                "--profile",
                "headless",
                "--json",
                "Reply in one short sentence: Garden demo is ready.",
            ],
        ):
            self.run(args, env=env, timeout=180)
        print(
            f"Agent ready: {garden} and {dsh}. Add {bin_dir} to PATH if needed.",
            flush=True,
        )

    def close(self):
        for proc in reversed(self.procs):
            if proc.poll() is None:
                if os.name == "nt":
                    try:
                        proc.send_signal(signal.CTRL_BREAK_EVENT)
                    except (OSError, ValueError):
                        proc.terminate()
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
        # One deadline for all children: waiting 15 s for each in turn can outlast the stop limit.
        deadline = time.monotonic() + CHILD_STOP_TIMEOUT
        for proc in self.procs:
            try:
                proc.wait(timeout=max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                end_process_tree(proc)
        for server in self.servers:
            server.shutdown()
            server.server_close()
        if getattr(self, "control_server", None):
            self.control_server.shutdown()
            self.control_server.server_close()
            if getattr(self, "control_thread", None):
                self.control_thread.join(timeout=5)
            manifest = load_manifest(self.manifest_file)
            for project in manifest["demo_projects"]:
                if project.get("root") == str(ROOT.resolve()):
                    project["active"] = False
                    project.pop("control_url", None)
            save_manifest(self.manifest_file, manifest)
        # Keep no login links or screenshots after the run; prefer the user's recoverable Trash.
        if shutil.which("trash"):
            subprocess.run(["trash", str(self.work)], check=False)


def demo_projects_for(data: dict, root: Path = ROOT) -> list[dict]:
    resolved = str(root.resolve())
    return [project for project in data["demo_projects"] if project.get("root") == resolved]


def _path_text(value: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(value).strip().strip('"')))


def login_recovery_paths(data: dict) -> list[Path]:
    """Files a later `--drop-login` run needs after a login the portal did not confirm.

    That is each login's launcher and Garden home, plus the bootstrap `just` and `uv` that run
    `just demo-uninstall`. Nothing is kept when no login remains.
    """
    if not data["logins"]:
        return []
    paths = []
    for login in data["logins"]:
        paths.append(Path(login["launcher"]))
        if login.get("garden_home"):
            paths.append(Path(login["garden_home"]))
    paths.extend(
        Path(entry["path"])
        for entry in data["artifacts"]
        if entry.get("owner") == "bootstrap" and Path(entry["path"]).name in RECOVERY_TOOLS
    )
    return paths


def login_recovery_dirs(data: dict) -> set[str]:
    """Folders whose PATH entries stay, so the kept launcher and recovery tools still resolve."""
    if not data["logins"]:
        return set()
    folders = {Path(login["launcher"]).parent for login in data["logins"]}
    folders.update(
        Path(entry["path"]).parent
        for entry in data["artifacts"]
        if entry.get("owner") == "bootstrap" and Path(entry["path"]).name in RECOVERY_TOOLS
    )
    return {_path_text(folder) for folder in folders}


def is_needed_for_recovery(path: Path, needed: list[Path]) -> bool:
    """True for a needed path, anything inside one, or a folder that holds one."""
    target = Path(os.path.abspath(path))
    for item in needed:
        kept = Path(os.path.abspath(item))
        if target == kept or kept in target.parents or target in kept.parents:
            return True
    return False


def path_entries_to_restore(entries: list[dict], keep_dirs: set[str]) -> list[dict]:
    return [
        entry
        for entry in entries
        if entry.get("scope") == "windows-user" and _path_text(entry["value"]) not in keep_dirs
    ]


# Dependency folders that the setup, bootstrap and demo flows may create inside a checkout.
DEPENDENCY_DIRS = ("backend/.venv", "agent/.venv", "frontend/node_modules")


def record_checkout_baseline(manifest_file: Path, root: Path = ROOT) -> None:
    """Note which dependency folders already exist before Garden first touches this checkout.

    Run before any setup step creates them. A later call for the same checkout changes nothing.
    """
    manifest = load_manifest(manifest_file)
    key = str(root.resolve())
    if key in manifest["checkouts"]:
        return
    manifest["checkouts"][key] = {
        "preexisting": [folder for folder in DEPENDENCY_DIRS if os.path.lexists(root / folder)]
    }
    save_manifest(manifest_file, manifest)


def record_created_dependencies(manifest_file: Path, root: Path = ROOT) -> list[str]:
    """Record the dependency folders that Garden created in this checkout, and return them."""
    manifest = load_manifest(manifest_file)
    baseline = manifest["checkouts"].get(str(root.resolve()))
    if baseline is None:
        # Without a baseline Garden cannot tell what it created, so it records nothing.
        return []
    recorded = []
    for folder in DEPENDENCY_DIRS:
        path = root / folder
        if folder in baseline["preexisting"] or not path.is_dir() or path.is_symlink():
            continue
        record_artifact(manifest, path, owner="demo-dependencies", kind="tree", root=path.parent)
        recorded.append(folder)
    if recorded:
        save_manifest(manifest_file, manifest)
    return recorded


def kept_dependency_lines(data: dict, root: Path = ROOT) -> list[str]:
    """Name each dependency folder that uninstall keeps, and why."""
    baseline = data["checkouts"].get(str(root.resolve()))
    recorded = {os.path.normcase(os.path.abspath(entry["path"])) for entry in data["artifacts"]}
    lines = []
    for folder in DEPENDENCY_DIRS:
        path = root / folder
        if not path.exists() or os.path.normcase(os.path.abspath(path)) in recorded:
            continue
        if baseline is not None and folder in baseline["preexisting"]:
            reason = "existed before Garden"
        else:
            reason = "not recorded as created by Garden"
        lines.append(f"kept: {reason}: {path}")
    return lines


def cleanup_artifacts(
    data: dict, root: Path = ROOT, keep: tuple[Path, ...] = ()
) -> tuple[dict, list[dict]]:
    selected = []
    retained = []
    for entry in data["artifacts"]:
        owner = entry.get("owner")
        path = Path(entry["path"])
        if keep and is_needed_for_recovery(path, list(keep)):
            retained.append(entry)
            continue
        if owner in {"bootstrap", "agent-installer"}:
            selected.append(entry)
            continue
        if owner in {"demo-dependencies", "demo-publish"}:
            try:
                path.resolve(strict=False).relative_to(root.resolve())
            except ValueError:
                retained.append(entry)
            else:
                selected.append(entry)
            continue
        retained.append(entry)
    scoped = {**data, "artifacts": selected}
    return scoped, artifact_plan(scoped)


def cleanup_preview(
    data: dict,
    mode: str,
    root: Path = ROOT,
    *,
    remove_env: bool = False,
    force: bool = False,
    drop_login: bool = False,
    env_file: Path | None = None,
    manifest_file: Path | None = None,
) -> list[str]:
    lines = []
    force = force or drop_login
    projects = demo_projects_for(data, root)
    for project in projects:
        lines.append(f"Stop demo runner for Compose project {project['name']}")
        lines.append(f"Compose down --volumes for project {project['name']}")
    if mode == "clean":
        if not projects:
            lines.append("No Garden demo project is recorded for this clone")
        lines.append("Keep Garden tools, installs, CLI login, PATH entries and .env")
        if data.get("logins"):
            lines.append(
                "The saved CLI login will no longer work once the demo database is removed"
            )
        return lines

    for login in data["logins"]:
        if force:
            lines.append(
                f"garden logout at {login['portal']} (remove local credentials and record "
                "even if portal revoke is unconfirmed)"
            )
        else:
            lines.append(
                f"garden logout at {login['portal']} (remote revoke, then local keyring removal)"
            )
    if not data["logins"]:
        lines.append("No Garden CLI login is recorded")
    project_images = {project.get("image") for project in projects if project.get("image")}
    for image in data["docker_images"]:
        if image.get("reference") in project_images:
            lines.append(f"Remove Garden image {image['reference']} ({image['id']}) if unchanged")
    # The plan assumes sign-out succeeds, which removes the login and so every recovery keep.
    # Only an unconfirmed sign-out keeps the recovery files, and those are listed separately.
    _scoped, plan = cleanup_artifacts(data, root)
    if data["logins"] and not force:
        _kept, fallback = cleanup_artifacts(data, root, keep=tuple(login_recovery_paths(data)))
        kept_paths = {_path_text(item["path"]) for item in fallback if item["action"] == "remove"}
        for item in plan:
            if item["action"] == "remove" and _path_text(item["path"]) not in kept_paths:
                lines.append(
                    f"If the portal does not confirm sign-out, keep for a later "
                    f"--drop-login: {item['path']}"
                )
    for item in plan:
        if item["action"] == "remove":
            if item["kind"] == "directory":
                lines.append(f"Remove if empty: {item['path']}")
            else:
                lines.append(f"Move to Trash: {item['path']}")
        elif item["action"] == "missing":
            lines.append(f"Already absent: {item['path']}")
        else:
            lines.append(f"Preserve {item['path']}: {item['reason']}")
    lines.extend(kept_dependency_lines(data, root))
    for entry in data["path_entries"]:
        lines.append(f"Remove Garden-added user PATH entry: {entry['value']}")
    if manifest_file:
        lines.append(
            f"Move the Garden install record {manifest_file} to Trash once nothing remains"
        )
    if env_file and env_file.exists():
        lines.append(
            f"Move {env_file} to Trash after separate confirmation"
            if remove_env
            else f"Keep {env_file} unless separately confirmed"
        )
    if not lines:
        lines.append("Nothing remains to uninstall")
    return lines


def cleanup_has_work(data: dict, mode: str, root: Path = ROOT) -> bool:
    if demo_projects_for(data, root):
        return True
    if mode == "clean":
        return False
    _scoped, plan = cleanup_artifacts(data, root)
    project_images = {project.get("image") for project in demo_projects_for(data, root)}
    return bool(
        data["logins"]
        or any(image.get("reference") in project_images for image in data["docker_images"])
        or data["path_entries"]
        or any(item["action"] not in {"missing"} for item in plan)
    )


def control_refused(error: Exception) -> bool:
    return isinstance(getattr(error, "reason", error), ConnectionRefusedError)


def control_answers(url: str) -> bool:
    """True while something still serves the runner's control URL. Refused means it is gone."""
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=b"", method="POST"), timeout=0.5
        ):
            return True
    except urllib.error.HTTPError:
        return True
    except OSError as error:
        return not control_refused(error)


def process_alive(pid: int) -> bool:
    """True while the process exists. When the check itself fails, it counts as alive."""
    if os.name == "nt":
        try:
            listing = run_bounded(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                timeout=DOCKER_TIMEOUT,
            )
        except (OSError, subprocess.SubprocessError):
            return True
        if listing.returncode:
            return True
        return f'"{pid}"' in listing.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie has ended but still answers signal 0 until its parent reaps it.
    with contextlib.suppress(OSError, IndexError):
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        return state != "Z"
    return True


def port_listening(port: int) -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def descendant_pids(pid: int) -> list[int]:
    """Every process below pid, from ps. Windows does not need this: taskkill /T ends the tree."""
    if os.name == "nt":
        return []
    try:
        listing = run_bounded(["ps", "-A", "-o", "pid=,ppid="], timeout=DOCKER_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in listing.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and all(field.isdigit() for field in fields):
            child, parent = int(fields[0]), int(fields[1])
            children.setdefault(parent, []).append(child)
    found: list[int] = []
    pending = [pid]
    while pending:
        for child in children.get(pending.pop(), []):
            found.append(child)
            pending.append(child)
    return found


def runner_gone(url: str, pids: list[int], ports: list[int]) -> bool:
    """True once the control URL is silent, no listed pid lives, and no demo port listens."""
    return (
        not control_answers(url)
        and not any(process_alive(pid) for pid in pids)
        and not any(port_listening(port) for port in ports)
    )


def runner_ports(project: dict) -> list[int]:
    """The API and web ports the runner's services listen on, recorded when it started."""
    return [project[key] for key in ("api_port", "web_port") if isinstance(project.get(key), int)]


def end_process_tree(proc: subprocess.Popen) -> None:
    """End a child and what it started, then poll until it has exited (bounded)."""
    if os.name == "nt":
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            run_bounded(["taskkill", "/PID", str(proc.pid), "/T", "/F"], timeout=DOCKER_TIMEOUT)
        if proc.poll() is None:
            with contextlib.suppress(OSError):
                proc.kill()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=RUNNER_GONE_TIMEOUT)


def wait_until(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while True:
        if condition():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)


class DemoCleanup:
    def __init__(self, args, mode: str, root: Path | None = None):
        self.args = args
        self.mode = mode
        self.root = root or ROOT
        self.manifest_file = manifest_path()
        self.data = load_manifest(self.manifest_file)
        self.projects = demo_projects_for(self.data, self.root)
        self.failed = False

    def confirm(self, preview: list[str]) -> bool:
        print("Garden demo cleanup preview:", flush=True)
        for line in preview:
            print(f"  {line}", flush=True)
        if self.args.dry_run or not cleanup_has_work(self.data, self.mode, self.root):
            return True
        if self.args.yes:
            return True
        if not sys.stdin.isatty():
            raise RuntimeError("Cleanup needs confirmation. Re-run with --yes or --dry-run.")
        return input("Proceed with cleanup? [y/N] ").strip().lower() in {"y", "yes"}

    def stop_runner(self, project: dict):
        """Stop a recorded demo runner, escalating to its process when it stays up too long.

        Returns True when the runner is gone or was never running, and False when it still answers
        after the process was terminated. Its Compose project is torn down either way.
        """
        url = project.get("control_url")
        name = project["name"]
        if not project.get("active") or not url:
            return True
        try:
            request = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status != 202:
                    raise RuntimeError(f"demo runner returned HTTP {response.status}")
        except (OSError, urllib.error.URLError, RuntimeError) as error:
            # Nothing listens on the control port once the demo was killed by hand, so the
            # runner is already gone and only Compose may still need tearing down.
            if control_refused(error):
                print(f"The demo runner for {name} is already stopped.", file=sys.stderr)
                return True
            print(f"Warning: the demo runner for {name} did not respond.", file=sys.stderr)
            return False
        pid = project.get("pid")
        ports = runner_ports(project)
        if wait_until(
            lambda: runner_gone(url, [int(pid)] if pid else [], ports),
            RUNNER_STOP_TIMEOUT,
        ):
            return True
        if not pid:
            print(
                f"Warning: the demo runner for {name} did not stop in {RUNNER_STOP_TIMEOUT}s, "
                "and its process is not recorded, so it was left running.",
                file=sys.stderr,
            )
            return False
        print(
            f"The demo runner for {name} did not stop in {RUNNER_STOP_TIMEOUT}s; "
            "ending its process.",
            file=sys.stderr,
        )
        if self.kill_runner(int(pid), url, ports):
            return True
        print(f"Warning: the demo runner for {name} is still running.", file=sys.stderr)
        return False

    def kill_runner(self, pid: int, url: str, ports: list[int]) -> bool:
        """End a runner that ignored the graceful stop, then poll until it and its tree are gone.

        Windows ends the whole tree with taskkill /T. POSIX signals the runner and the processes
        it started, taken from ps before the signal so none is orphaned and missed.
        """
        if os.name == "nt":
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                run_bounded(["taskkill", "/PID", str(pid), "/T", "/F"], timeout=DOCKER_TIMEOUT)
            return wait_until(lambda: runner_gone(url, [pid], ports), RUNNER_GONE_TIMEOUT)
        tree = [pid, *descendant_pids(pid)]
        for sig, grace in (
            (signal.SIGTERM, RUNNER_KILL_TIMEOUT / 2),
            (signal.SIGKILL, RUNNER_GONE_TIMEOUT),
        ):
            for target in tree:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(target, sig)
            if wait_until(lambda: runner_gone(url, tree, ports), grace):
                return True
        return False

    def compose_down(self, project: dict) -> bool:
        compose = Path(project.get("root", ROOT)) / "dev" / "compose.yaml"
        if not compose.exists():
            print(
                f"Warning: cannot find {compose}; project {project['name']} was kept "
                "in the manifest.",
                file=sys.stderr,
            )
            return False
        env = os.environ.copy()
        env["COMPOSE_PROJECT_NAME"] = project["name"]
        if os.name == "nt":
            env["WSLENV"] = wsl_env(env.get("WSLENV", ""), COMPOSE_ENV_KEYS)
        try:
            result = run_bounded(
                command_for(
                    [
                        "docker",
                        "compose",
                        "--project-name",
                        project["name"],
                        "-f",
                        "dev/compose.yaml",
                        "down",
                        "--volumes",
                        "--remove-orphans",
                    ],
                    path=env.get("PATH"),
                ),
                timeout=COMPOSE_TIMEOUT,
                cwd=compose.parent.parent,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as error:
            print(
                f"Warning: Compose cleanup for {project['name']} did not finish: {error}",
                file=sys.stderr,
            )
            return False
        if result.returncode:
            print(
                f"Warning: Compose cleanup for {project['name']} failed: "
                f"{result.stderr.strip() or result.stdout.strip()}",
                file=sys.stderr,
            )
            return False
        return True

    def logout(self):
        for login in list(self.data["logins"]):
            launcher = Path(login["launcher"])
            force = bool(
                getattr(getattr(self, "args", None), "force", False)
                or getattr(getattr(self, "args", None), "drop_login", False)
            )
            if not launcher.exists():
                if force:
                    # Without the launcher the portal cannot be asked, so only the record goes.
                    print(
                        f"Warning: garden is missing at {launcher}, so sign-out for "
                        f"{login['portal']} was not sent to the portal. The login record was "
                        "dropped (--drop-login). The portal login remains valid until it expires "
                        "or is revoked in the portal. The local keyring entry, if any, could not "
                        "be removed.",
                        file=sys.stderr,
                    )
                    self.data["logins"].remove(login)
                    continue
                print(
                    f"Warning: cannot revoke {login['portal']} because {launcher} is missing; "
                    "the local keyring entry may remain.",
                    file=sys.stderr,
                )
                self.failed = True
                continue
            env = os.environ.copy()
            env["GARDEN_API_URL"] = login["portal"]
            if login.get("garden_home"):
                env["GARDEN_HOME"] = login["garden_home"]
            # Give the child CLI a network timeout that finishes well before the subprocess limit,
            # so remote timeouts trigger the forced local deletion instead of getting killed.
            client_timeout = env.get("GARDEN_CLIENT_TIMEOUT")
            try:
                if client_timeout is None or float(client_timeout) >= 30:
                    env["GARDEN_CLIENT_TIMEOUT"] = "10"
            except ValueError:
                env["GARDEN_CLIENT_TIMEOUT"] = "10"
            cmd = [str(launcher), "logout", "--json"]
            if force:
                cmd.append("--force")
            try:
                result = run_bounded(
                    command_for(cmd, path=env.get("PATH")),
                    env=env,
                    timeout=LOGOUT_TIMEOUT,
                )
            except (OSError, subprocess.SubprocessError) as error:
                print(
                    f"Warning: garden logout failed for {login['portal']}: {error}",
                    file=sys.stderr,
                )
                print(
                    f"The portal login for {login['portal']} could not be revoked and remains "
                    "valid until it expires or is revoked in the portal.",
                    file=sys.stderr,
                )
                self.failed = True
                continue

            warning = next(
                (line for line in result.stderr.splitlines() if "portal did not confirm" in line),
                None,
            )
            forced_drop = (
                result.returncode == 0 and "local login was dropped (--force)" in result.stderr
            )
            if forced_drop:
                message = warning or result.stderr.strip()
                if message.lower().startswith("warning: "):
                    message = message[9:].strip()
                print(f"Warning: {message}", file=sys.stderr)
                print(
                    f"Warning: the portal did not confirm sign-out for {login['portal']}. "
                    "The local login record was removed (--force). "
                    "The server-side login remains valid on the portal until it expires or is "
                    "revoked.",
                    file=sys.stderr,
                )
                self.data["logins"].remove(login)
                continue

            if result.returncode or warning:
                message = warning or result.stderr.strip() or result.stdout.strip()
                if message.lower().startswith("warning: "):
                    message = message[9:].strip()
                print(f"Warning: {message}", file=sys.stderr)
                print(
                    f"The portal login for {login['portal']} could not be revoked and remains "
                    "valid until it expires or is revoked in the portal.",
                    file=sys.stderr,
                )
                self.failed = True
                continue
            print(f"Signed out of {login['portal']}.", flush=True)
            self.data["logins"].remove(login)

    def remove_images(self, references: set[str]):
        selected = [
            image for image in self.data["docker_images"] if image.get("reference") in references
        ]
        if not selected:
            return

        def docker(args, timeout=IMAGE_TIMEOUT):
            # A hung or missing Docker reports a failed command rather than blocking cleanup.
            try:
                return run_bounded(command_for(args), timeout=timeout)
            except (OSError, subprocess.SubprocessError) as error:
                return subprocess.CompletedProcess(args, 1, "", str(error))

        available = docker(["docker", "info"], timeout=DOCKER_TIMEOUT)
        if available.returncode:
            print(
                f"Warning: Docker is unavailable; recorded demo images were kept: "
                f"{available.stderr.strip() or available.stdout.strip()}",
                file=sys.stderr,
            )
            self.failed = True
            return
        retained = []
        for image in self.data["docker_images"]:
            reference = image.get("reference")
            image_id = image.get("id")
            if reference not in references:
                retained.append(image)
                continue
            inspect = docker(["docker", "image", "inspect", "--format", "{{.Id}}", reference])
            current_id = inspect.stdout.strip() if inspect.returncode == 0 else ""
            if current_id == image_id:
                result = docker(["docker", "image", "rm", reference])
                if result.returncode:
                    print(
                        f"Warning: could not remove {reference}: {result.stderr.strip()}",
                        file=sys.stderr,
                    )
                    retained.append(image)
                    self.failed = True
                continue
            inspect_id = docker(
                ["docker", "image", "inspect", "--format", "{{json .RepoTags}}", image_id]
            )
            if inspect_id.returncode:
                continue
            try:
                tags = json.loads(inspect_id.stdout)
            except ValueError:
                tags = ["unknown"]
            if not tags:
                result = docker(["docker", "image", "rm", image_id])
                if result.returncode:
                    print(
                        f"Warning: could not remove Garden image {image_id}: "
                        f"{result.stderr.strip()}",
                        file=sys.stderr,
                    )
                    retained.append(image)
                    self.failed = True
            elif current_id:
                print(
                    f"Preserving {reference}; it now points to a different image.",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Preserving Garden image {image_id}; it has another image tag: "
                    f"{', '.join(tags)}.",
                    file=sys.stderr,
                )
        self.data["docker_images"] = retained

    def restore_user_path(self, keep_dirs: set[str] = frozenset()):
        if os.name != "nt":
            return
        entries = path_entries_to_restore(self.data["path_entries"], set(keep_dirs))
        if not entries:
            return
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS
        ) as key:
            try:
                current, value_type = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                current, value_type = "", winreg.REG_EXPAND_SZ
            updated, removed = without_owned_path_entries(
                {**self.data, "path_entries": entries}, current, scope="windows-user", separator=";"
            )
            self.data["path_entries"] = [
                entry for entry in self.data["path_entries"] if entry not in entries
            ]
            if not removed:
                return
            if updated:
                winreg.SetValueEx(key, "Path", 0, value_type, updated)
            elif any(entry.get("created_value") for entry in entries):
                with contextlib.suppress(FileNotFoundError):
                    winreg.DeleteValue(key, "Path")
            else:
                winreg.SetValueEx(key, "Path", 0, value_type, "")

    def save(self):
        """Persist progress after each step, so an interrupted run can be finished by rerunning.

        When nothing remains, the manifest itself is moved to Trash.
        """
        nothing_left = not (
            self.data["artifacts"]
            or self.data["path_entries"]
            or self.data["logins"]
            or self.data["demo_projects"]
            or self.data["docker_images"]
        )
        if nothing_left:
            with contextlib.suppress(OSError):
                trash_path(self.manifest_file)
                self.manifest_file.parent.rmdir()
            return
        save_manifest(self.manifest_file, self.data)

    def run(self):
        force = bool(getattr(self.args, "force", False) or getattr(self.args, "drop_login", False))
        preview = cleanup_preview(
            self.data,
            self.mode,
            self.root,
            remove_env=self.args.remove_env,
            force=force,
            env_file=Path(self.args.env_file).resolve(),
            manifest_file=self.manifest_file,
        )
        if not self.confirm(preview):
            print("Cleanup cancelled.", flush=True)
            return 0
        if self.args.dry_run:
            return 0
        try:
            self.apply()
        finally:
            self.save()
        return 1 if self.failed else 0

    def apply(self):
        if self.mode == "uninstall":
            self.logout()
            self.save()
        stopped_projects = []
        for project in self.projects:
            if not self.stop_runner(project):
                # The runner is still up; still tear down whatever it left in Compose.
                self.failed = True
            if self.compose_down(project):
                project["active"] = False
                project.pop("control_url", None)
                project.pop("pid", None)
                stopped_projects.append(project)
            else:
                self.failed = True
            self.save()
        if self.mode != "uninstall":
            return
        project_images = {
            project.get("image") for project in stopped_projects if project.get("image")
        }
        self.remove_images(project_images)
        self.save()
        # An unconfirmed login still needs its launcher, Garden home and the just/uv that run
        # this command, so those files and their PATH folders stay until --drop-login.
        needed = login_recovery_paths(self.data)
        self.restore_user_path(login_recovery_dirs(self.data))
        self.save()
        scoped, plan = cleanup_artifacts(self.data, self.root, keep=tuple(needed))
        updated, warnings = apply_artifact_plan(scoped, plan)
        for warning in warnings:
            print(f"Warning: {warning}", file=sys.stderr)
        if warnings:
            self.failed = True
        self.data["artifacts"] = updated["artifacts"] + [
            entry for entry in self.data["artifacts"] if entry not in scoped["artifacts"]
        ]
        self.save()
        if self.data["logins"]:
            for login in self.data["logins"]:
                launcher = Path(login["launcher"])
                if launcher.exists():
                    print(
                        f"Kept garden at {launcher}, its Garden home and the login record "
                        f"for {login['portal']}, because the portal did not confirm sign-out.",
                        flush=True,
                    )
            print("To finish, run: just demo-uninstall --drop-login", flush=True)
        self.data["demo_projects"] = [
            project for project in self.data["demo_projects"] if project not in stopped_projects
        ]
        self.save()
        for line in kept_dependency_lines(self.data, self.root):
            print(line, flush=True)
        self.ask_about_env_file()

    def ask_about_env_file(self):
        # Only --remove-env or an interactive answer moves .env. --yes confirms the listed
        # cleanup and never prompts, so an unattended run cannot wait here.
        env_file = Path(self.args.env_file).resolve()
        if not env_file.exists():
            return
        if self.args.remove_env:
            trash_path(env_file)
        elif sys.stdin.isatty() and not self.args.yes:
            if input(f"Also move {env_file} to Trash? [y/N] ").strip().lower() in {"y", "yes"}:
                trash_path(env_file)
        else:
            print(f"Kept {env_file}; pass --remove-env to move it to Trash.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    cleanup_modes = parser.add_mutually_exclusive_group()
    cleanup_modes.add_argument(
        "--clean",
        action="store_true",
        help="Stop the demo and remove its Compose volumes",
    )
    cleanup_modes.add_argument(
        "--uninstall",
        action="store_true",
        help="Stop the demo and remove recorded Garden installs",
    )
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument(
        "--fake-model",
        action="store_true",
        help="Use and save the local fake upstream selection",
    )
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument(
        "--persona", choices=["Dev Admin", "Marcus Lee", "Priya Nair", "Lena Fischer"]
    )
    parser.add_argument("--env-file", default=os.environ.get("GARDEN_DEMO_ENV_FILE", ".env"))
    parser.add_argument(
        "--check-model",
        action="store_true",
        help="Validate/persist model settings, then exit without starting the stack",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Exit after startup and verification (for isolated end-to-end tests)",
    )
    parser.add_argument(
        "--check-docker",
        action="store_true",
        help="Check Docker CLI, Compose and daemon availability, then exit",
    )
    parser.add_argument("--yes", action="store_true", help="Confirm the cleanup preview")
    parser.add_argument(
        "--dry-run", action="store_true", help="Show cleanup without changing anything"
    )
    parser.add_argument(
        "--remove-env",
        action="store_true",
        help="Separately opt in to moving the selected .env file to Trash during uninstall",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Remove local credentials and manifest login records during uninstall "
            "even when the portal cannot confirm revocation (server session may remain active)"
        ),
    )
    parser.add_argument(
        "--drop-login",
        dest="force",
        action="store_true",
        help="Alias for --force",
    )
    parser.add_argument(
        "--record-checkout-baseline",
        action="store_true",
        help="Note which dependency folders already exist in this checkout, then exit",
    )
    parser.add_argument(
        "--record-checkout-dependencies",
        action="store_true",
        help="Record the dependency folders that setup created in this checkout, then exit",
    )
    args = parser.parse_args()
    if args.record_checkout_baseline:
        record_checkout_baseline(manifest_path())
        return 0
    if args.record_checkout_dependencies:
        record_created_dependencies(manifest_path())
        return 0
    if args.clean or args.uninstall:
        if (
            args.web_only
            or args.fake_model
            or args.non_interactive
            or args.persona
            or args.check_model
            or args.check_docker
            or args.smoke
        ):
            parser.error("Cleanup options cannot be combined with demo startup options")
        return DemoCleanup(args, "uninstall" if args.uninstall else "clean").run()
    if args.yes or args.dry_run or args.remove_env or args.force:
        parser.error("--yes, --dry-run, --remove-env and --force require --clean or --uninstall")
    # The first `just demo` step runs this before any dependency install, so the baseline is exact.
    record_checkout_baseline(manifest_path())
    if args.check_docker:
        if not args.check_model:
            check_docker()
        return 0
    if args.non_interactive and not args.web_only and not args.check_model and not args.persona:
        parser.error("--non-interactive requires --persona")
    if not args.check_model:
        check_docker()
    demo = Demo(args)

    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        if not stopping:
            stopping = True
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        if not args.web_only:
            demo.configure_model()
        if args.check_model:
            return 0
        demo.start_control()
        demo.setup()
        if not args.web_only:
            demo.publish()
        demo.stack()
        if not args.web_only:
            demo.install()
        print("Demo ready. Press Ctrl+C to stop.", flush=True)
        if not args.smoke:
            while not demo.stop_event.wait(0.5):
                demo.check_processes()
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        demo.close()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, OSError, subprocess.SubprocessError, ValueError) as error:
        # Do not echo upstream HTTP bodies, tokens, or child environments.
        print(f"Demo failed: {type(error).__name__}: {error}", file=sys.stderr)
        sys.exit(1)
