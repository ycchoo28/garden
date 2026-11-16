"""Stopping a demo runner: a bounded poll after each kill, and the whole process tree ended."""

import importlib.util
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "demo_cleanup", Path(__file__).resolve().parents[1] / "demo.py"
)
assert SPEC and SPEC.loader
DEMO = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DEMO
SPEC.loader.exec_module(DEMO)

posix_only = pytest.mark.skipif(os.name == "nt", reason="uses POSIX signals and process groups")

# Writes its pid only after it has set itself to ignore SIGTERM, so a test can rely on that.
IGNORING_CHILD = (
    "import os, signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "tmp = sys.argv[1] + '.tmp'\n"
    "open(tmp, 'w').write(str(os.getpid()))\n"
    "os.replace(tmp, sys.argv[1])\n"
    "time.sleep(60)\n"
)


def _wait_for(predicate, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for the test process"
        time.sleep(0.05)


def _closed_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}/stop/gone"


def _spawn_runner_tree(tmp_path: Path) -> tuple[subprocess.Popen, int]:
    """A runner-like parent that ignores SIGTERM and starts a grandchild that does too."""
    pid_file = tmp_path / "grandchild.pid"
    parent_script = (
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]])\n"
        "time.sleep(60)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", parent_script, IGNORING_CHILD, str(pid_file)],
        start_new_session=True,
    )
    _wait_for(lambda: pid_file.exists() and pid_file.read_text().isdigit())
    return parent, int(pid_file.read_text())


def _cleanup() -> DEMO.DemoCleanup:
    return object.__new__(DEMO.DemoCleanup)


@posix_only
def test_kill_ends_a_runner_that_ignores_the_stop_and_its_grandchild(tmp_path, monkeypatch):
    monkeypatch.setattr(DEMO, "RUNNER_KILL_TIMEOUT", 4)
    parent, grandchild = _spawn_runner_tree(tmp_path)

    # The graceful signal did nothing; only the SIGKILL escalation ends the tree.
    assert _cleanup().kill_runner(parent.pid, _closed_url(), []) is True

    assert parent.poll() is not None
    assert not DEMO.process_alive(grandchild)


@posix_only
def test_a_pid_that_lingers_briefly_after_the_kill_is_waited_for(monkeypatch):
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    real_alive = DEMO.process_alive
    polls = []

    def lingering(pid: int) -> bool:
        # The first few checks after the kill still see the process, as on a slow Windows exit.
        polls.append(pid)
        return True if len(polls) <= 3 else real_alive(pid)

    monkeypatch.setattr(DEMO, "process_alive", lingering)
    try:
        assert _cleanup().kill_runner(child.pid, _closed_url(), []) is True
    finally:
        child.kill()
        child.wait()
    assert len(polls) > 3
    assert child.poll() is not None


def test_a_runner_that_never_exits_is_reported_after_a_bounded_wait(monkeypatch):
    monkeypatch.setattr(DEMO, "RUNNER_KILL_TIMEOUT", 0.2)
    monkeypatch.setattr(DEMO, "RUNNER_GONE_TIMEOUT", 0.5)
    monkeypatch.setattr(DEMO, "process_alive", lambda _pid: True)
    started = time.monotonic()

    # No such pid exists, so the signals are no-ops; the process stays "alive" to the check.
    assert _cleanup().kill_runner(999_999_999, _closed_url(), []) is False

    assert time.monotonic() - started < 5


def test_windows_kill_uses_the_tree_kill_and_polls_until_gone(monkeypatch):
    calls = []
    polls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    def lingering(_pid: int) -> bool:
        polls.append(_pid)
        return len(polls) <= 2

    monkeypatch.setattr(DEMO.os, "name", "nt")
    monkeypatch.setattr(DEMO, "run_bounded", fake_run)
    monkeypatch.setattr(DEMO, "process_alive", lingering)

    assert _cleanup().kill_runner(4321, _closed_url(), []) is True
    assert calls == [["taskkill", "/PID", "4321", "/T", "/F"]]
    assert len(polls) == 3


def test_windows_process_check_reads_tasklist(monkeypatch):
    monkeypatch.setattr(DEMO.os, "name", "nt")

    def listing(stdout: str):
        return lambda command, **_kwargs: subprocess.CompletedProcess(command, 0, stdout, "")

    monkeypatch.setattr(DEMO, "run_bounded", listing('"python.exe","4321","Console","1","9 K"\n'))
    assert DEMO.process_alive(4321) is True

    monkeypatch.setattr(DEMO, "run_bounded", listing("INFO: No tasks are running which match.\n"))
    assert DEMO.process_alive(4321) is False


def test_runner_ports_come_from_the_recorded_project():
    project = {"api_port": 55000, "web_port": 55173, "pid": 1}

    assert DEMO.runner_ports(project) == [55000, 55173]
    assert DEMO.runner_ports({"pid": 1}) == []


@posix_only
def test_close_shares_one_deadline_across_children(tmp_path, monkeypatch):
    monkeypatch.setattr(DEMO, "CHILD_STOP_TIMEOUT", 1)
    children = []
    for _ in range(3):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                IGNORING_CHILD,
                str(tmp_path / f"c{len(children)}.pid"),
            ],
            start_new_session=True,
        )
        children.append(child)
    _wait_for(lambda: all((tmp_path / f"c{i}.pid").exists() for i in range(3)))

    runner = object.__new__(DEMO.Demo)
    runner.procs = children
    runner.servers = []
    runner.control_server = None
    runner.control_thread = None
    runner.work = tmp_path / "work"
    started = time.monotonic()
    try:
        runner.close()
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    elapsed = time.monotonic() - started

    # Three children that ignore SIGTERM cost one shared second, not three.
    assert elapsed < 2.5
    assert all(child.poll() is not None for child in children)


def test_control_check_counts_a_connect_timeout_as_gone(monkeypatch):
    # Windows does not refuse a closed loopback port at once: it retries the connect for about
    # two seconds, so a short check sees a timeout. That must not read as a live runner.
    def slow_refusal(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(DEMO.socket, "create_connection", slow_refusal)
    monkeypatch.setattr(DEMO.urllib.request, "urlopen", slow_refusal)

    assert DEMO.control_answers(_closed_url()) is False


def test_control_check_sees_a_live_listener():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        url = f"http://127.0.0.1:{listener.getsockname()[1]}/stop/token"

        assert DEMO.control_answers(url) is True
    assert DEMO.control_answers(url) is False


def test_runner_gone_after_a_windows_style_slow_refusal(monkeypatch):
    def slow_refusal(*_args, **_kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(DEMO.socket, "create_connection", slow_refusal)
    monkeypatch.setattr(DEMO.urllib.request, "urlopen", slow_refusal)
    monkeypatch.setattr(DEMO, "process_alive", lambda _pid: False)
    monkeypatch.setattr(DEMO, "port_listening", lambda _port: False)

    assert DEMO.runner_gone(_closed_url(), [4321], [55000]) is True


def _docker_fake(leftovers: dict[str, list[str]], compose_code: int = 0):
    """A docker CLI fake: compose down exits with compose_code and removes nothing."""
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):
        command = [str(part) for part in command]
        calls.append(command)
        args = command[1:]
        if args[:1] == ["compose"]:
            return subprocess.CompletedProcess(command, compose_code, "", "compose failed")
        kind = "container" if args[0] in {"ps", "rm"} else args[0]
        if "ls" in args or args[:1] == ["ps"]:
            return subprocess.CompletedProcess(
                command, 0, "".join(f"{i}\n" for i in leftovers[kind]), ""
            )
        if "rm" in args:
            for ident in args[args.index("rm") + 1 :]:
                if ident in leftovers[kind]:
                    leftovers[kind].remove(ident)
            return subprocess.CompletedProcess(command, 0, "", "")
        raise AssertionError(command)

    return calls, fake_run


def test_compose_teardown_removes_containers_left_after_a_forced_stop(monkeypatch):
    leftovers = {"container": ["c1", "c2"], "volume": ["garden-demo-x_pgdata"], "network": ["n1"]}
    calls, fake_run = _docker_fake(leftovers)
    monkeypatch.setattr(DEMO, "run_bounded", fake_run)
    monkeypatch.setattr(DEMO, "command_for", lambda args, path=None: list(args))
    project = {"name": "garden-demo-x", "root": str(DEMO.ROOT)}

    assert _cleanup().compose_down(project) is True

    assert leftovers == {"container": [], "volume": [], "network": []}
    label = "label=com.docker.compose.project=garden-demo-x"
    assert any(label in call for call in calls)


def test_compose_teardown_sweeps_even_when_compose_down_fails(monkeypatch, capsys):
    leftovers = {"container": ["c1"], "volume": [], "network": []}
    _calls, fake_run = _docker_fake(leftovers, compose_code=1)
    monkeypatch.setattr(DEMO, "run_bounded", fake_run)
    monkeypatch.setattr(DEMO, "command_for", lambda args, path=None: list(args))
    project = {"name": "garden-demo-x", "root": str(DEMO.ROOT)}

    # Nothing of the project is left, so the teardown counts as done despite the warning.
    assert _cleanup().compose_down(project) is True
    assert leftovers["container"] == []
    assert "compose failed" in capsys.readouterr().err


def test_compose_teardown_fails_when_a_container_cannot_be_removed(monkeypatch):
    leftovers = {"container": ["stuck"], "volume": [], "network": []}
    _calls, fake_run = _docker_fake(leftovers)

    def stubborn(command, **kwargs):
        if "rm" in [str(part) for part in command]:
            return subprocess.CompletedProcess(command, 1, "", "cannot remove")
        return fake_run(command, **kwargs)

    monkeypatch.setattr(DEMO, "run_bounded", stubborn)
    monkeypatch.setattr(DEMO, "command_for", lambda args, path=None: list(args))
    project = {"name": "garden-demo-x", "root": str(DEMO.ROOT)}

    assert _cleanup().compose_down(project) is False
