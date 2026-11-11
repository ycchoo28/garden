"""Opt-in end-to-end checks of the public demo commands against the dev Postgres container."""

import contextlib
import os
import signal
import socket
import subprocess
import sys
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


def terminate_process_tree(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        result = subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
        if result.returncode and process.poll() is None:
            process.terminate()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
            if process.poll() is None:
                process.kill()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def cleanup_demo(process, sql, databases):
    try:
        if process and process.poll() is None:
            terminate_process_tree(process)
    finally:
        for database in databases:
            subprocess.run(
                [*sql, f'drop database "{database}" with (force)'],
                cwd=ROOT,
                check=True,
            )


@pytest.mark.parametrize("mode", ["demo-web", "demo"])
def test_demo_from_a_fresh_home_and_environment(tmp_path, mode):
    database = f"garden_demo_{uuid.uuid4().hex}_test"
    just_database = f"garden_demo_just_{uuid.uuid4().hex}_test"
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
        if mode == "demo-web":
            file.write(
                "GARDEN_AGENT_MODEL_UPSTREAM_URL=http://127.0.0.1:9/v1\n"
                "GARDEN_AGENT_MODEL_API_KEY=saved-web-only-token\n"
                "GARDEN_AGENT_MODEL_MODELS=saved-web-only-model\n"
                "GARDEN_AGENT_MODEL_DEFAULT=saved-web-only-model\n"
                "GARDEN_DEMO_FAKE_MODEL=1\n"
            )
    just_env_file = tmp_path / "just.env"
    just_env_file.write_text(
        f"GARDEN_DATABASE_URL=postgresql+psycopg://garden:garden-dev@127.0.0.1:55432/{just_database}\n"
        f"GARDEN_FILES_ROOT={tmp_path}/just-files\n"
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("GARDEN_")}
    env.update(
        {
            "HOME": str(tmp_path),
            "USERPROFILE": str(tmp_path),
            "GARDEN_DEMO_ENV_FILE": str(env_file),
            "GARDEN_DEMO_JUST_DOTENV_PATH": str(just_env_file),
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
    command = ["just", "--dotenv-path", str(just_env_file), mode, "--non-interactive"]
    if mode == "demo":
        command += ["--fake-model", "--persona", "Marcus Lee"]
    process = None
    created_databases = []
    log = tmp_path / "demo.log"
    try:
        for test_database in (database, just_database):
            subprocess.run([*sql, f'create database "{test_database}"'], cwd=ROOT, check=True)
            created_databases.append(test_database)
        with log.open("w") as output:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
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
            query_sql = sql[:-1]
            query_sql[query_sql.index("-d") + 1] = database
            query_sql.extend(["-At", "-c", "select to_regclass('users.person') is not null"])
            database_has_users = subprocess.run(
                query_sql,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            assert database_has_users.stdout.strip() == "t"
            just_database_sql = query_sql.copy()
            just_database_sql[just_database_sql.index("-d") + 1] = just_database
            just_database_result = subprocess.run(
                just_database_sql,
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            assert just_database_result.stdout.strip() == "f"
            if mode == "demo-web":
                assert not (tmp_path / "bin").exists()
                saved = env_file.read_text()
                assert "saved-web-only-token" in saved
                assert "GARDEN_AGENT_MODEL_UPSTREAM_URL=http://127.0.0.1:9/v1" in saved
                assert "GARDEN_DEMO_FAKE_MODEL=1" in saved
            else:
                assert "Garden demo is ready." in log.read_text()
                assert "Marcus Lee" in log.read_text()
                patch = (tmp_path / "dsh/cordis.patch.yml").read_text()
                assert f"127.0.0.1:{api_port}/api/agent/model/v1" in patch
                assert "local-demo-only" not in patch
                assert "local-demo-only" not in (tmp_path / "garden/config.json").read_text()
    finally:
        cleanup_demo(process, sql, created_databases)
    assert "Traceback" not in log.read_text(), f"demo teardown failed; log: {log}"
    for port in (api_port, web_port, embed_port):
        with socket.socket() as probe:
            assert probe.connect_ex(("127.0.0.1", port)) != 0, f"demo left port {port} open"


def test_database_drop_runs_if_process_termination_fails(monkeypatch):
    commands = []

    class RunningProcess:
        pid = 123

        @staticmethod
        def poll():
            return None

    def fail_termination(_process):
        raise OSError("process tree could not be stopped")

    def record_drop(command, **kwargs):
        commands.append((command, kwargs))

    monkeypatch.setattr(sys.modules[__name__], "terminate_process_tree", fail_termination)
    monkeypatch.setattr(subprocess, "run", record_drop)
    with pytest.raises(OSError, match="could not be stopped"):
        cleanup_demo(RunningProcess(), ["docker", "compose", "exec", "psql", "-c"], ["garden_test"])
    assert commands[0][0][-1] == 'drop database "garden_test" with (force)'
