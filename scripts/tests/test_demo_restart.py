"""Starting the demo again after a failed run, a Ctrl+C or an uninstall."""

import importlib.util
import subprocess
import sys
import types
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "demo_restart", Path(__file__).resolve().parents[1] / "demo.py"
)
assert SPEC and SPEC.loader
DEMO = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DEMO
SPEC.loader.exec_module(DEMO)


def _demo() -> DEMO.Demo:
    demo = object.__new__(DEMO.Demo)
    demo.project = "garden-demo-abc"
    demo.env = {"PATH": "/usr/bin"}
    demo.api_port, demo.web_port = 55000, 55173
    demo.compose_postgres_port, demo.compose_oidc_port = 55433, 55082
    return demo


def _docker_ps(stdout: str, calls: list):
    def fake_run(command, **_kwargs):
        calls.append([str(part) for part in command])
        return subprocess.CompletedProcess(command, 0, stdout, "")

    return fake_run


def test_ports_of_the_projects_own_running_containers_are_not_checked(monkeypatch):
    # A failed run or a Ctrl+C leaves this project's Postgres up; Compose reuses it.
    calls: list = []
    monkeypatch.setattr(DEMO, "run_bounded", _docker_ps("c1\nc2\n", calls))
    monkeypatch.setattr(DEMO, "command_for", lambda args, path=None: list(args))

    assert _demo().ports_to_check() == [55000, 55173]
    assert any("label=com.docker.compose.project=garden-demo-abc" in call for call in calls)


def test_all_ports_are_checked_when_the_project_has_no_containers(monkeypatch):
    monkeypatch.setattr(DEMO, "run_bounded", _docker_ps("", []))
    monkeypatch.setattr(DEMO, "command_for", lambda args, path=None: list(args))

    assert _demo().ports_to_check() == [55000, 55173, 55433, 55082]


def test_all_ports_are_checked_when_docker_cannot_answer(monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("docker missing")

    monkeypatch.setattr(DEMO, "run_bounded", broken)
    monkeypatch.setattr(DEMO, "command_for", lambda args, path=None: list(args))

    assert _demo().ports_to_check() == [55000, 55173, 55433, 55082]


def test_windows_user_path_is_read_again_so_a_newly_installed_node_is_found(monkeypatch):
    # An uninstall removes the bootstrap Node; the installer puts a new one on the user PATH,
    # which this already running process has not seen yet.
    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER="HKCU",
        OpenKey=lambda *_args: _Key(),
        QueryValueEx=lambda _key, name: (r"C:\Users\me\.garden\tools\node", 2),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(DEMO.os, "name", "nt")
    env = {"PATH": r"C:\bin"}

    DEMO.with_user_path(env)

    assert env["PATH"] == r"C:\Users\me\.garden\tools\node;C:\bin"


def test_user_path_is_left_alone_off_windows(monkeypatch):
    monkeypatch.setattr(DEMO.os, "name", "posix")
    env = {"PATH": "/usr/bin"}

    DEMO.with_user_path(env)

    assert env == {"PATH": "/usr/bin"}


class _Key:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False
