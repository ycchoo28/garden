"""Opt-in end-to-end checks of the public demo commands against the dev Postgres container."""

import os
import signal
import socket
import subprocess
import time
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("GARDEN_DEMO_E2E") != "1", reason="run with just demo-e2e"
)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.mark.parametrize("mode", ["demo-web", "demo"])
def test_demo_from_a_fresh_home_and_environment(tmp_path, mode):
    database = f"garden_demo_{uuid.uuid4().hex}_test"
    sql = [
        "docker",
        "compose",
        "-f",
        str(ROOT / "dev/compose.yaml"),
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
    ]
    api_port, web_port, embed_port = free_port(), free_port(), free_port()
    env_file = tmp_path / ".env"
    env_file.write_text((ROOT / ".env.example").read_text())
    with env_file.open("a") as file:
        file.write(
            f"\nGARDEN_DATABASE_URL=postgresql+psycopg://garden:garden-dev@127.0.0.1:55432/{database}\n"
            f"GARDEN_FILES_ROOT={tmp_path}/files\n"
            f"GARDEN_AGENT_DOWNLOADS_DIR={tmp_path}/downloads\n"
        )
    env = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
    env.update(
        {
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "GARDEN_DEMO_ENV_FILE": str(env_file),
            "GARDEN_HOME": str(tmp_path / "garden"),
            "GARDEN_DSH_HOME": str(tmp_path / "dsh"),
            "GARDEN_BIN_DIR": str(tmp_path / "bin"),
            "GARDEN_SECRET_STORE": f"file:{tmp_path}/secrets.json",
            "GARDEN_DEMO_API_PORT": str(api_port),
            "GARDEN_DEMO_WEB_PORT": str(web_port),
            "GARDEN_DEMO_EMBEDDING_PORT": str(embed_port),
            "GARDEN_DEMO_MODEL_PORT": "0",
            "PLAYWRIGHT_BROWSERS_PATH": os.environ.get(
                "PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "browsers")
            ),
        }
    )
    command = ["just", mode, "--non-interactive"]
    if mode == "demo":
        command += ["--fake-model", "--persona", "Marcus Lee"]
    subprocess.run([*sql, f'create database "{database}"'], cwd=ROOT, check=True)
    process = None
    log = tmp_path / "demo.log"
    try:
        with log.open("w") as output:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            deadline = time.monotonic() + 1200
            while "Demo ready. Press Ctrl+C" not in log.read_text(errors="replace"):
                assert process.poll() is None, f"{mode} exited; log: {log}"
                assert time.monotonic() < deadline, f"{mode} timed out; log: {log}"
                time.sleep(0.5)
            for url in (
                f"http://127.0.0.1:{api_port}/api/health",
                f"http://localhost:{web_port}/",
                f"http://localhost:{web_port}/api/health",
            ):
                with urllib.request.urlopen(url, timeout=10) as response:
                    assert response.status == 200
            if mode == "demo-web":
                assert not (tmp_path / "bin").exists()
                assert "GARDEN_DEMO_FAKE_MODEL=0" in env_file.read_text()
            else:
                assert "Garden demo is ready." in log.read_text()
                assert "Marcus Lee" in log.read_text()
                patch = (tmp_path / "dsh/cordis.patch.yml").read_text()
                assert f"127.0.0.1:{api_port}/api/agent/model/v1" in patch
                assert "local-demo-only" not in patch
                assert "local-demo-only" not in (tmp_path / "garden/config.json").read_text()
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        subprocess.run([*sql, f'drop database "{database}" with (force)'], cwd=ROOT, check=True)
    assert "Traceback" not in log.read_text(), f"demo teardown failed; log: {log}"
    for port in (api_port, web_port, embed_port):
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0, f"demo left port {port} open"
