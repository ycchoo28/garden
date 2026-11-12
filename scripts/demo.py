"""Cross-platform local demo lifecycle. Model credentials belong only to the portal."""

from __future__ import annotations

import argparse
import contextlib
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


def request_json(url: str, key: str = "", body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(
        url, headers=headers, data=None if body is None else json.dumps(body).encode()
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def validate_model(env: dict[str, str]) -> bool:
    url, key, models, default = (env.get(name, "").strip() for name in MODEL_KEYS)
    allowed = [name.strip() for name in models.split(",") if name.strip()]
    if (
        not url.startswith(("http://", "https://"))
        or not key
        or not allowed
        or default not in allowed
    ):
        return False
    try:
        listed = request_json(url.rstrip("/") + "/models", key)
        available = {row["id"] for row in listed["data"]}
        if not set(allowed).issubset(available):
            return False
        # Models can be visible while the token cannot invoke them. Check the selected default too.
        reply = request_json(
            url.rstrip("/") + "/chat/completions",
            key,
            {
                "model": default,
                "messages": [{"role": "user", "content": "Reply: ready"}],
                "max_tokens": 8,
            },
        )
        return bool(reply.get("choices"))
    except (OSError, ValueError, KeyError, TypeError):
        return False


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
        for folder in ("backend", "agent"):
            venv_dir = ROOT / folder / ".venv"
            venv_dir_existed = venv_dir.exists()
            venv_python = venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            if not venv_python.exists():
                self.run(["uv", "sync", "--frozen"], ROOT / folder)
                if not venv_dir_existed:
                    manifest = load_manifest(self.manifest_file)
                    record_artifact(
                        manifest,
                        venv_dir,
                        owner="demo-dependencies",
                        kind="tree",
                        root=venv_dir.parent,
                    )
                    save_manifest(self.manifest_file, manifest)
        frontend_modules = ROOT / "frontend/node_modules"
        if not frontend_modules.exists():
            command = ["bun", "install", "--frozen-lockfile"]
            if os.name == "nt":
                # Bun 1.4.3's default Windows link backend produced InvalidBinContent shims.
                command.append("--backend=copyfile")
            self.run(command, ROOT / "frontend")
            manifest = load_manifest(self.manifest_file)
            record_artifact(
                manifest,
                frontend_modules,
                owner="demo-dependencies",
                kind="tree",
                root=frontend_modules.parent,
            )
            save_manifest(self.manifest_file, manifest)

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

    def configure_model(self):
        valid = validate_model(self.env)
        fake = self.args.fake_model or (self.env.get(FAKE_FLAG) == "1" and not valid)
        if fake:
            saved_keys = {
                key: value
                for key, value in load_env(self.env_file).items()
                if key in (MODEL_KEYS[0], MODEL_KEYS[1]) and value.strip()
            }
            self.fake_model()
            if not saved_keys:
                save_env(self.env_file, {FAKE_FLAG: "1", MODEL_KEYS[3]: "garden-demo"})
            return
        if valid:
            print("Reusing validated model upstream.", flush=True)
        else:
            if self.args.non_interactive or not sys.stdin.isatty():
                raise RuntimeError(
                    "Model upstream is missing or unusable. Supply the four GARDEN_AGENT_MODEL_* "
                    "settings or use --fake-model --non-interactive."
                )
            print("Model upstream is missing or unusable. The key stays only in the portal .env.")
            while True:
                url = input("OpenAI-compatible base URL, or 'fake' for a local demo: ").strip()
                if url == "fake":
                    saved_keys = {
                        key: value
                        for key, value in load_env(self.env_file).items()
                        if key in (MODEL_KEYS[0], MODEL_KEYS[1]) and value.strip()
                    }
                    self.fake_model()
                    if not saved_keys:
                        save_env(self.env_file, {FAKE_FLAG: "1", MODEL_KEYS[3]: "garden-demo"})
                    return
                key = getpass.getpass("API key/token: ")
                try:
                    names = [
                        row["id"] for row in request_json(url.rstrip("/") + "/models", key)["data"]
                    ]
                    print("Available models: " + ", ".join(names))
                except (OSError, ValueError, KeyError, TypeError):
                    print("Could not list models. Check the URL and token.")
                    continue
                models = input("Allowed model name(s), comma separated: ").strip()
                default = (
                    input(f"Default model [{models.split(',')[0].strip()}]: ").strip()
                    or models.split(",")[0].strip()
                )
                self.env.update(
                    dict(
                        zip(
                            MODEL_KEYS,
                            (url.rstrip("/"), key, models, default),
                            strict=True,
                        )
                    )
                )
                self.env[FAKE_FLAG] = "0"
                if validate_model(self.env):
                    break
                print("The upstream rejected the model check. Try again.")
        if not validate_model(self.env):
            raise RuntimeError("Model validation failed")
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
        for proc in self.procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                    if proc.poll() is None:
                        proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
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


def cleanup_artifacts(data: dict, root: Path = ROOT) -> tuple[dict, list[dict]]:
    selected = []
    retained = []
    for entry in data["artifacts"]:
        owner = entry.get("owner")
        path = Path(entry["path"])
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
    _scoped, plan = cleanup_artifacts(data, root)
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
    for entry in data["path_entries"]:
        lines.append(f"Remove Garden-added user PATH entry: {entry['value']}")
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


class DemoCleanup:
    def __init__(self, args, mode: str):
        self.args = args
        self.mode = mode
        self.manifest_file = manifest_path()
        self.data = load_manifest(self.manifest_file)
        self.projects = demo_projects_for(self.data)
        self.failed = False

    def confirm(self, preview: list[str]) -> bool:
        print("Garden demo cleanup preview:", flush=True)
        for line in preview:
            print(f"  {line}", flush=True)
        if self.args.dry_run or not cleanup_has_work(self.data, self.mode):
            return True
        if self.args.yes:
            return True
        if not sys.stdin.isatty():
            raise RuntimeError("Cleanup needs confirmation. Re-run with --yes or --dry-run.")
        return input("Proceed with cleanup? [y/N] ").strip().lower() in {"y", "yes"}

    def stop_runner(self, project: dict):
        url = project.get("control_url")
        if not project.get("active") or not url:
            return True
        try:
            request = urllib.request.Request(url, data=b"", method="POST")
            with urllib.request.urlopen(request, timeout=3) as response:
                if response.status != 202:
                    raise RuntimeError(f"demo runner returned HTTP {response.status}")
        except (OSError, urllib.error.URLError, RuntimeError):
            print(
                f"Warning: the demo runner for {project['name']} did not respond.",
                file=sys.stderr,
            )
            return False
        for _ in range(100):
            try:
                request = urllib.request.Request(url, data=b"", method="POST")
                with urllib.request.urlopen(request, timeout=0.5):
                    pass
            except OSError:
                return True
            time.sleep(0.2)
        else:
            print(
                f"Warning: the demo runner for {project['name']} did not stop in time.",
                file=sys.stderr,
            )
            return False
        return True

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
        result = subprocess.run(
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
            cwd=compose.parent.parent,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
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
            if not launcher.exists():
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
            force = bool(
                getattr(getattr(self, "args", None), "force", False)
                or getattr(getattr(self, "args", None), "drop_login", False)
            )
            cmd = [str(launcher), "logout", "--json"]
            if force:
                cmd.append("--force")
            try:
                result = subprocess.run(
                    command_for(cmd, path=env.get("PATH")),
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
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
        available = subprocess.run(
            command_for(["docker", "info"]), capture_output=True, text=True, check=False
        )
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
            inspect = subprocess.run(
                command_for(["docker", "image", "inspect", "--format", "{{.Id}}", reference]),
                capture_output=True,
                text=True,
                check=False,
            )
            current_id = inspect.stdout.strip() if inspect.returncode == 0 else ""
            if current_id == image_id:
                result = subprocess.run(
                    command_for(["docker", "image", "rm", reference]),
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if result.returncode:
                    print(
                        f"Warning: could not remove {reference}: {result.stderr.strip()}",
                        file=sys.stderr,
                    )
                    retained.append(image)
                    self.failed = True
                continue
            inspect_id = subprocess.run(
                command_for(
                    [
                        "docker",
                        "image",
                        "inspect",
                        "--format",
                        "{{json .RepoTags}}",
                        image_id,
                    ]
                ),
                capture_output=True,
                text=True,
                check=False,
            )
            if inspect_id.returncode:
                continue
            try:
                tags = json.loads(inspect_id.stdout)
            except ValueError:
                tags = ["unknown"]
            if not tags:
                result = subprocess.run(
                    command_for(["docker", "image", "rm", image_id]),
                    capture_output=True,
                    text=True,
                    check=False,
                )
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

    def restore_user_path(self):
        if os.name != "nt":
            return
        entries = [
            item for item in self.data["path_entries"] if item.get("scope") == "windows-user"
        ]
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
                self.data, current, scope="windows-user", separator=";"
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

    def run(self):
        force = bool(getattr(self.args, "force", False) or getattr(self.args, "drop_login", False))
        preview = cleanup_preview(
            self.data,
            self.mode,
            remove_env=self.args.remove_env,
            force=force,
            env_file=Path(self.args.env_file).resolve(),
        )
        if not self.confirm(preview):
            print("Cleanup cancelled.", flush=True)
            return 0
        if self.args.dry_run:
            return 0

        if self.mode == "uninstall":
            self.logout()
        stopped_projects = []
        for project in self.projects:
            if not self.stop_runner(project):
                # The runner may already be gone; still tear down whatever it left in Compose.
                self.failed = True
            if self.compose_down(project):
                project["active"] = False
                project.pop("control_url", None)
                stopped_projects.append(project)
            else:
                self.failed = True
        if self.mode == "uninstall":
            project_images = {
                project.get("image") for project in stopped_projects if project.get("image")
            }
            self.remove_images(project_images)
            self.restore_user_path()
            scoped, plan = cleanup_artifacts(self.data)
            updated, warnings = apply_artifact_plan(scoped, plan)
            for warning in warnings:
                print(f"Warning: {warning}", file=sys.stderr)
            if warnings:
                self.failed = True
            self.data["artifacts"] = updated["artifacts"] + [
                entry for entry in self.data["artifacts"] if entry not in scoped["artifacts"]
            ]
            self.data["demo_projects"] = [
                project for project in self.data["demo_projects"] if project not in stopped_projects
            ]
            env_file = Path(self.args.env_file).resolve()
            if env_file.exists() and (
                self.args.remove_env
                or (
                    sys.stdin.isatty()
                    and input(f"Also move {env_file} to Trash? [y/N] ").strip().lower()
                    in {"y", "yes"}
                )
            ):
                trash_path(env_file)
        save_manifest(self.manifest_file, self.data)
        if (
            not self.data["artifacts"]
            and not self.data["path_entries"]
            and not self.data["logins"]
            and not self.data["demo_projects"]
            and not self.data["docker_images"]
        ):
            try:
                trash_path(self.manifest_file)
                self.manifest_file.parent.rmdir()
            except (OSError, FileNotFoundError):
                pass
        return 1 if self.failed else 0


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
    args = parser.parse_args()
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
