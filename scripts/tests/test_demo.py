"""Model setup exercised through the same command boundary as Just, with isolated state."""

import argparse
import importlib.util
import os
import subprocess
import sys
import threading
import types
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("demo", ROOT / "scripts/demo.py")
assert SPEC and SPEC.loader
DEMO = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DEMO
SPEC.loader.exec_module(DEMO)


def run_model(path, *args, extra=None, stdin_text=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
    env.update({"HOME": str(path.parent), "GARDEN_DEMO_MODEL_PORT": "0", **(extra or {})})
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo.py"),
            "--env-file",
            str(path),
            "--check-model",
            *args,
        ],
        env=env,
        input=stdin_text,
        stdin=None if stdin_text is not None else subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_fake_selection_is_saved_and_reused_without_a_prompt(tmp_path):
    path = tmp_path / ".env"
    first = run_model(path, "--fake-model", "--non-interactive")
    assert first.returncode == 0, first.stderr
    saved = dotenv_values(path)
    assert saved["GARDEN_DEMO_FAKE_MODEL"] == "1"
    assert saved["GARDEN_AGENT_MODEL_DEFAULT"] == "garden-demo"
    second = run_model(path, "--non-interactive")
    assert second.returncode == 0, second.stderr
    assert path.stat().st_mode & 0o077 == 0
    assert "local-demo-only" not in first.stdout + first.stderr


def test_fake_model_does_not_clobber_saved_real_model(tmp_path):
    class Upstream(DEMO.FakeModel):
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer real-secret-token":
                self.send_error(401)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        path = tmp_path / ".env"
        real_settings = dict(
            zip(
                DEMO.MODEL_KEYS,
                (url, "real-secret-token", "garden-demo", "garden-demo"),
                strict=True,
            )
        )
        first = run_model(path, "--non-interactive", extra=real_settings)
        assert first.returncode == 0, first.stderr
        saved_first = dotenv_values(path)
        assert saved_first[DEMO.MODEL_KEYS[1]] == "real-secret-token"
        assert saved_first.get(DEMO.FAKE_FLAG, "0") != "1"

        # Running with --fake-model overrides in-memory for this run without clobbering .env
        faked = run_model(path, "--fake-model", "--non-interactive")
        assert faked.returncode == 0, faked.stderr
        saved_faked = dotenv_values(path)
        assert saved_faked[DEMO.MODEL_KEYS[1]] == "real-secret-token"
        assert saved_faked[DEMO.MODEL_KEYS[0]] == url
        assert saved_faked.get(DEMO.FAKE_FLAG, "0") != "1"

        # Subsequent run without --fake-model reuses the saved real upstream
        again = run_model(path, "--non-interactive")
        assert again.returncode == 0, again.stderr
        assert "Reusing validated" in again.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_interactive_fake_selection_does_not_clobber_saved_real_model(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.touch(mode=0o600)
    real_settings = dict(
        zip(
            DEMO.MODEL_KEYS,
            (
                "http://127.0.0.1:54321/v1",
                "real-secret-token",
                "custom-model",
                "custom-model",
            ),
            strict=True,
        )
    )
    DEMO.save_env(path, real_settings)

    args = argparse.Namespace(
        fake_model=False,
        non_interactive=False,
        web_only=False,
        env_file=str(path),
    )
    monkeypatch.setenv("GARDEN_DEMO_MODEL_PORT", "0")
    demo = DEMO.Demo(args)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "4")

    try:
        demo.configure_model()
        saved = dotenv_values(path)
        assert saved[DEMO.MODEL_KEYS[0]] == "http://127.0.0.1:54321/v1"
        assert saved[DEMO.MODEL_KEYS[1]] == "real-secret-token"
        assert saved[DEMO.MODEL_KEYS[3]] == "custom-model"
        assert saved.get(DEMO.FAKE_FLAG, "0") != "1"
    finally:
        demo.close()


def test_missing_model_fails_without_waiting_for_input(tmp_path):
    result = run_model(tmp_path / ".env", "--non-interactive")
    assert result.returncode != 0
    assert "missing or unusable" in result.stderr
    assert "--fake-model" in result.stderr


@pytest.mark.parametrize("fault", ["none", "token", "model", "completion", "unreachable"])
def test_saved_external_configuration_is_actually_validated(tmp_path, fault):
    class Upstream(DEMO.FakeModel):
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer test-private-token":
                self.send_error(401)
            else:
                super().do_GET()

        def do_POST(self):
            if fault == "completion":
                self.send_error(403)
            else:
                super().do_POST()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        path = tmp_path / ".env"
        settings = dict(
            zip(
                DEMO.MODEL_KEYS,
                (url, "test-private-token", "garden-demo", "garden-demo"),
                strict=True,
            )
        )
        if fault == "token":
            settings[DEMO.MODEL_KEYS[1]] = "rejected-token"
        if fault == "model":
            settings[DEMO.MODEL_KEYS[2]] = settings[DEMO.MODEL_KEYS[3]] = "absent-model"
        if fault == "unreachable":
            server.shutdown()
            server.server_close()
        result = run_model(path, "--non-interactive", extra=settings)
        assert (result.returncode == 0) == (fault == "none"), result.stderr
        assert "test-private-token" not in result.stdout + result.stderr
        if fault == "none":
            assert dotenv_values(path)[DEMO.MODEL_KEYS[1]] == "test-private-token"
            again = run_model(path, "--non-interactive")
            assert again.returncode == 0, again.stderr
            assert "Reusing validated" in again.stdout
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_noninteractive_full_demo_requires_an_explicit_persona(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/demo.py"),
            "--fake-model",
            "--non-interactive",
            "--env-file",
            str(tmp_path / ".env"),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "--persona" in result.stderr
    assert not (tmp_path / ".env").exists()


def test_just_dotenv_does_not_override_selected_env_and_shell_overrides_win(tmp_path):
    just_upstream = ThreadingHTTPServer(("127.0.0.1", 0), DEMO.FakeModel)
    selected_requests = []

    class SelectedUpstream(DEMO.FakeModel):
        def do_GET(self):
            selected_requests.append(self.headers.get("Authorization"))
            if self.headers.get("Authorization") != "Bearer explicit-shell-token":
                self.send_error(401)
            else:
                super().do_GET()

    selected_upstream = ThreadingHTTPServer(("127.0.0.1", 0), SelectedUpstream)
    for server in (just_upstream, selected_upstream):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    just_env = tmp_path / "just.env"
    just_env.write_text(
        "\n".join(
            (
                f"GARDEN_AGENT_MODEL_UPSTREAM_URL=http://127.0.0.1:{just_upstream.server_port}/v1",
                "GARDEN_AGENT_MODEL_API_KEY=just-file-token",
                "GARDEN_AGENT_MODEL_MODELS=garden-demo",
                "GARDEN_AGENT_MODEL_DEFAULT=garden-demo",
                "GARDEN_DATABASE_URL=postgresql+psycopg://garden:garden-dev@127.0.0.1:55432/just_dev",
                "GARDEN_FILES_ROOT=/just/files",
            )
        )
        + "\n"
    )
    selected_env = tmp_path / "selected.env"
    selected_env.write_text(
        "\n".join(
            (
                f"GARDEN_AGENT_MODEL_UPSTREAM_URL=http://127.0.0.1:{selected_upstream.server_port}/v1",
                "GARDEN_AGENT_MODEL_API_KEY=selected-file-token",
                "GARDEN_AGENT_MODEL_MODELS=garden-demo",
                "GARDEN_AGENT_MODEL_DEFAULT=garden-demo",
                "GARDEN_DATABASE_URL=postgresql+psycopg://garden:garden-dev@127.0.0.1:55432/selected_test",
                "GARDEN_FILES_ROOT=/selected/files",
            )
        )
        + "\n"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
    env.update(
        {
            "GARDEN_DEMO_JUST_DOTENV_PATH": str(just_env),
            "GARDEN_AGENT_MODEL_API_KEY": "explicit-shell-token",
            "HOME": str(tmp_path),
        }
    )
    try:
        result = subprocess.run(
            [
                "just",
                "--dotenv-path",
                str(just_env),
                "demo",
                "--check-model",
                "--env-file",
                str(selected_env),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        # Every upstream request uses the shell token, never the .env one.
        assert selected_requests
        assert set(selected_requests) == {"Bearer explicit-shell-token"}
        selected_text = selected_env.read_text()
        assert (
            "GARDEN_DATABASE_URL=postgresql+psycopg://garden:garden-dev@127.0.0.1:55432/selected_test"
            in selected_text
        )
        assert "GARDEN_FILES_ROOT=/selected/files" in selected_text
        assert "just-file-token" not in selected_text
    finally:
        for server in (just_upstream, selected_upstream):
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize(
    "arguments",
    [
        ("--fake-model", "--check-model", "--non-interactive"),
        ("--check-model", "--fake-model", "--non-interactive"),
    ],
)
def test_just_demo_model_check_flag_order_does_not_require_docker(tmp_path, arguments):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if os.name == "nt":
        docker = bin_dir / "docker.cmd"
        docker.write_text("@exit /b 1\r\n")
    else:
        docker = bin_dir / "docker"
        docker.write_text("#!/bin/sh\nexit 1\n")
        docker.chmod(0o755)
    env_file = tmp_path / "demo.env"
    just_env_file = tmp_path / "just.env"
    just_env_file.write_text("")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
    env.update(
        {
            "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "GARDEN_DEMO_JUST_DOTENV_PATH": str(just_env_file),
            "GARDEN_DEMO_MODEL_PORT": "0",
        }
    )
    result = subprocess.run(
        [
            "just",
            "--dotenv-path",
            str(just_env_file),
            "demo",
            *arguments,
            "--env-file",
            str(env_file),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert dotenv_values(env_file)[DEMO.FAKE_FLAG] == "1"


def test_web_only_explicitly_disables_saved_model_settings(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "\n".join(
            (
                "GARDEN_DATABASE_URL=postgresql+psycopg://garden:garden-dev@127.0.0.1:55432/garden_test",
                "GARDEN_AGENT_MODEL_UPSTREAM_URL=https://model.example/v1",
                "GARDEN_AGENT_MODEL_API_KEY=saved-token",
                "GARDEN_AGENT_MODEL_MODELS=saved-model",
                "GARDEN_AGENT_MODEL_DEFAULT=saved-model",
                "GARDEN_DEMO_FAKE_MODEL=1",
            )
        )
        + "\n"
    )
    monkeypatch.setattr(DEMO.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path / "work"))
    demo = DEMO.Demo(types.SimpleNamespace(env_file=path, web_only=True))
    assert all(demo.env[key] == "" for key in DEMO.MODEL_KEYS)
    assert demo.env[DEMO.FAKE_FLAG] == "0"
    assert all(key not in demo.client_env() for key in DEMO.MODEL_KEYS)

    monkeypatch.syspath_prepend(str(ROOT / "backend"))
    from app.config import Settings

    for key in DEMO.MODEL_KEYS:
        monkeypatch.setenv(key, demo.env[key])
    settings = Settings(_env_file=path)
    assert settings.agent_model_upstream_url == ""
    assert settings.agent_model_api_key.get_secret_value() == ""
    assert settings.agent_model_models == ""
    assert settings.agent_model_default == ""


def test_windows_batch_commands_use_cmd_and_preserve_arguments(monkeypatch):
    executable = r"C:\test tools\docker.cmd"
    monkeypatch.setattr(DEMO, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(DEMO.shutil, "which", lambda _name, path=None: executable)

    assert DEMO.command_for(["docker", "compose", "version"], path="test") == [
        "cmd.exe",
        "/d",
        "/c",
        'call "C:\\test tools\\docker.cmd" compose version',
    ]


@pytest.mark.parametrize(
    ("platform", "executable"),
    [
        ("posix", "/usr/local/bin/docker"),
        ("nt", r"C:\Program Files\Docker\docker.exe"),
    ],
)
def test_command_for_keeps_non_batch_executables_unchanged(monkeypatch, platform, executable):
    monkeypatch.setattr(DEMO, "os", types.SimpleNamespace(name=platform))
    monkeypatch.setattr(DEMO.shutil, "which", lambda _name, path=None: executable)

    assert DEMO.command_for(["docker", "compose", "version"], path="test") == [
        executable,
        "compose",
        "version",
    ]


def test_windows_demo_services_use_process_groups_and_timeout_fallback(monkeypatch, tmp_path):
    demo = object.__new__(DEMO.Demo)
    demo.procs = []
    demo.servers = []
    demo.work = tmp_path
    monkeypatch.setattr(DEMO, "os", types.SimpleNamespace(name="nt"))
    monkeypatch.setattr(DEMO, "signal", types.SimpleNamespace(CTRL_BREAK_EVENT=1))
    monkeypatch.setattr(DEMO.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    monkeypatch.setattr(DEMO.shutil, "which", lambda _name, path=None: None)
    start_calls = []
    process = types.SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(
        DEMO.subprocess,
        "Popen",
        lambda command, **options: start_calls.append((command, options)) or process,
    )
    demo.env = {"PATH": ""}
    demo.start(["service.exe"])
    assert start_calls[0][1]["creationflags"] == 512
    assert "start_new_session" not in start_calls[0][1]

    class TimedOutProcess:
        pid = 42

        def __init__(self):
            self.returncode = None
            self.signals = []
            self.waits = 0
            self.killed = False

        def poll(self):
            return self.returncode

        def send_signal(self, value):
            self.signals.append(value)

        def wait(self, timeout):
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("service", timeout)
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    timed_out = TimedOutProcess()
    demo.procs = [timed_out]
    monkeypatch.setattr(
        DEMO.subprocess, "run", lambda *_args, **_kwargs: types.SimpleNamespace(returncode=1)
    )
    demo.close()
    assert timed_out.signals == [1]
    assert timed_out.killed


def test_demo_uses_explicit_postgres_image_tag(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("")
    monkeypatch.setattr(DEMO.tempfile, "mkdtemp", lambda **_kwargs: str(tmp_path / "work"))
    demo = DEMO.Demo(types.SimpleNamespace(env_file=env_file, web_only=True))
    demo.env["GARDEN_DEV_POSTGRES_IMAGE"] = "garden-postgres:isolated-test"
    commands = []

    def stop_after_build(args, **_kwargs):
        commands.append(args)
        raise RuntimeError("stop after isolated image build command")

    monkeypatch.setattr(demo, "run", stop_after_build)
    with pytest.raises(RuntimeError, match="stop after isolated image build command"):
        demo.stack()
    build = commands[0]
    assert build[build.index("-t") + 1] == "garden-postgres:isolated-test"


@pytest.mark.parametrize("system", ["Linux", "Darwin", "Windows"])
def test_missing_docker_cli_reports_os_guidance_before_initializing_env(
    tmp_path, monkeypatch, system
):
    env_file = tmp_path / "not-created.env"
    monkeypatch.setattr(DEMO.platform, "system", lambda: system)
    monkeypatch.setattr(DEMO.shutil, "which", lambda _name: None)
    monkeypatch.setattr(sys, "argv", ["demo.py", "--web-only", "--env-file", str(env_file)])
    with pytest.raises(RuntimeError, match="Docker CLI was not found") as error:
        DEMO.main()
    assert DEMO.DOCKER_GUIDANCE[system] in str(error.value)
    if system == "Windows":
        assert "Docker CLI installed only inside WSL is not detected" in str(error.value)
    assert not env_file.exists()


@pytest.mark.parametrize(
    ("unavailable", "message"),
    [("compose", "Docker Compose"), ("daemon", "Docker daemon")],
)
def test_docker_preflight_checks_compose_and_daemon(tmp_path, monkeypatch, unavailable, message):
    calls = []
    monkeypatch.setattr(DEMO.shutil, "which", lambda _name: "/usr/bin/docker")

    def fail_requested(command, **_kwargs):
        calls.append(command)
        if (unavailable == "compose") == (command[1] == "compose"):
            raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(DEMO.subprocess, "run", fail_requested)
    with pytest.raises(RuntimeError, match=message):
        DEMO.check_docker()
    if unavailable == "daemon":
        assert [call[1] for call in calls] == ["compose", "info"]
    else:
        assert [call[1] for call in calls] == ["compose"]


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX terminal behavior; Windows is checked on win-test"
)
def test_interactive_repair_hides_the_key_and_reuses_saved_settings(tmp_path):
    import pty
    import select
    import time

    secret = "test-private$token#with'quotes"

    class Upstream(DEMO.FakeModel):
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer " + secret:
                self.send_error(401)
            else:
                super().do_GET()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / ".env"
    path.write_text("GARDEN_AGENT_MODEL_UPSTREAM_URL=http://127.0.0.1:1/v1\n")
    master, slave = pty.openpty()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
    env["HOME"] = str(tmp_path)
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts/demo.py"), "--check-model", "--env-file", str(path)],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    os.close(slave)
    transcript = ""
    upstream_url = f"http://127.0.0.1:{server.server_port}/v1"
    prompts = [
        ("Provider [", "3\n"),
        ("OpenAI-compatible base URL", upstream_url + "\n"),
        ("API key or token", secret + "\n"),
        ("Filter, numbers or ids", "\n"),
        ("Filter, numbers or ids", "1\n"),
        ("Default model [", "\n"),
        ("Save these settings", "y\n"),
    ]
    offset = 0
    try:
        for marker, answer in prompts:
            deadline = time.monotonic() + 15
            while marker not in transcript[offset:] and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    transcript += os.read(master, 65536).decode()
            assert marker in transcript[offset:], transcript
            offset = transcript.index(marker, offset) + len(marker)
            os.write(master, answer.encode())
        assert process.wait(timeout=20) == 0
        while select.select([master], [], [], 0.1)[0]:
            try:
                transcript += os.read(master, 65536).decode()
            except OSError:
                break
        assert secret not in transcript
        assert dotenv_values(path)[DEMO.MODEL_KEYS[1]] == secret
        reused = run_model(path, "--non-interactive")
        assert reused.returncode == 0, reused.stderr
        assert "Reusing validated" in reused.stdout
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        server.shutdown()
        server.server_close()
        thread.join()


def test_wsl_env_adds_compose_settings_once_and_keeps_existing_entries():
    assert DEMO.wsl_env("", ("A", "B")) == "A:B"
    assert DEMO.wsl_env("KEEP/p:A", ("A", "B")) == "KEEP/p:A:B"
