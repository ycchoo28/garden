"""Model setup exercised through the same command boundary as Just, with isolated state."""

import importlib.util
import os
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("demo", ROOT / "scripts/demo.py")
assert SPEC and SPEC.loader
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


def run_model(path, *args, extra=None):
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
        stdin=subprocess.DEVNULL,
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
    )
    os.close(slave)
    transcript = ""
    prompts = [
        ("local demo: ", f"http://127.0.0.1:{server.server_port}/v1\n"),
        ("API key/token: ", secret + "\n"),
        ("comma separated: ", "garden-demo\n"),
        ("Default model [garden-demo]: ", "\n"),
    ]
    try:
        for marker, answer in prompts:
            deadline = time.monotonic() + 15
            while marker not in transcript and time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    transcript += os.read(master, 65536).decode()
            assert marker in transcript, transcript
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
