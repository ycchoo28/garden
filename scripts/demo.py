"""Cross-platform local demo lifecycle. Model credentials belong only to the portal."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import os
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

ROOT = Path(__file__).resolve().parents[1]
MODEL_KEYS = tuple(
    "GARDEN_AGENT_MODEL_" + name for name in ("UPSTREAM_URL", "API_KEY", "MODELS", "DEFAULT")
)
FAKE_FLAG = "GARDEN_DEMO_FAKE_MODEL"


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
        # Explicit shell values take precedence over .env, as with Just.
        self.env.update(
            {key: value for key, value in os.environ.items() if key.startswith("GARDEN_")}
        )
        self.procs = []
        self.servers = []
        self.work = Path(tempfile.mkdtemp(prefix="garden-demo-"))
        self.api_port = int(self.env.get("GARDEN_DEMO_API_PORT", "55000"))
        self.web_port = int(self.env.get("GARDEN_DEMO_WEB_PORT", "55173"))
        self.api = f"http://127.0.0.1:{self.api_port}"
        self.web = f"http://localhost:{self.web_port}"
        self.env["GARDEN_PUBLIC_URL"] = self.web
        self.env.pop("VIRTUAL_ENV", None)

    def run(self, args, cwd=ROOT, env=None, **kwargs):
        # Resolve .cmd commands explicitly on Windows (npm, bun, and installed launchers).
        command = shutil.which(str(args[0]), path=(env or self.env).get("PATH")) or str(args[0])
        return subprocess.run(
            [command, *map(str, args[1:])],
            cwd=cwd,
            env=env or self.env,
            check=True,
            **kwargs,
        )

    def start(self, args, cwd=ROOT, env=None):
        effective = env or self.env
        executable = shutil.which(str(args[0]), path=effective.get("PATH")) or str(args[0])
        proc = subprocess.Popen(
            [executable, *map(str, args[1:])],
            cwd=cwd,
            env=effective,
            start_new_session=os.name != "nt",
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
        for port in (self.api_port, self.web_port):
            with socket.socket() as probe:
                probe.bind(("127.0.0.1", port))
        for folder in ("backend", "agent"):
            if not (
                ROOT
                / folder
                / ".venv"
                / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            ).exists():
                self.run(["uv", "sync", "--frozen"], ROOT / folder)
        if not (ROOT / "frontend/node_modules").exists():
            command = ["bun", "install", "--frozen-lockfile"]
            if os.name == "nt":
                command.append("--backend=copyfile")
            self.run(command, ROOT / "frontend")

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
        fake = self.args.fake_model or self.env.get(FAKE_FLAG) == "1"
        if fake:
            self.fake_model()
        elif validate_model(self.env):
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
                    self.fake_model()
                    break
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
                    dict(zip(MODEL_KEYS, (url.rstrip("/"), key, models, default), strict=True))
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
        destination.mkdir(parents=True, exist_ok=True)
        self.env["GARDEN_AGENT_DOWNLOADS_DIR"] = str(destination)
        for name in (
            "garden.pyz",
            "dsh-bundle.tar.gz",
            "dsh-bundle-windows-x64.tar.gz",
        ):
            # Windows serves its host bundle under its own name.
            source = ROOT / "agent/dist" / name
            if source.exists():
                shutil.copy2(source, destination / name)
        for name in ("install.sh", "install.ps1"):
            shutil.copy2(ROOT / "agent/install" / name, destination / name)
        shutil.copy2(ROOT / "agent/install/dsh/cordis.patch.yml", destination / "dsh-config.yml")
        commit = self.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout
        (destination / "build-commit").write_text(commit)

    def stack(self):
        # Do not execute shell-only Just recipes: this path also runs natively on Windows.
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
                "garden-postgres:local",
                "infra/postgres",
            ]
        )
        self.env["GARDEN_DEV_POSTGRES_IMAGE"] = "garden-postgres:local"
        self.run(["docker", "compose", "-f", "dev/compose.yaml", "up", "-d", "--wait"])
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
        self.start(
            [
                "uv",
                "run",
                "uvicorn",
                "app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.api_port),
            ],
            ROOT / "backend",
        )
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
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(proc.pid, signal.SIGTERM)
        for proc in self.procs:
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
        for server in self.servers:
            server.shutdown()
            server.server_close()
        # Keep no login links or screenshots after the run; prefer the user's recoverable Trash.
        if shutil.which("trash"):
            subprocess.run(["trash", str(self.work)], check=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
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
    args = parser.parse_args()
    if args.non_interactive and not args.web_only and not args.check_model and not args.persona:
        parser.error("--non-interactive requires --persona")
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
        demo.run(["docker", "info"], stdout=subprocess.DEVNULL)
        demo.setup()
        if not args.web_only:
            demo.publish()
        demo.stack()
        if not args.web_only:
            demo.install()
        print("Demo ready. Press Ctrl+C to stop.", flush=True)
        if not args.smoke:
            while True:
                demo.check_processes()
                time.sleep(1)
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
