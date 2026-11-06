"""Shared fixtures: a fake portal on a loopback port, and an engine that talks to it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fake_portal import GOOD_TOKEN, FakePortal

from garden.auth import store_token as auth_store_token
from garden.engine import Engine, PortalClient

ROOT = Path(__file__).resolve().parents[1]

Sleep = Callable[[float], None]


@pytest.fixture
def portal() -> Iterator[FakePortal]:
    fake = FakePortal()
    fake.start()
    yield fake
    fake.stop()


@pytest.fixture
def make_engine(portal: FakePortal, tmp_path: Path) -> Callable[..., Engine]:
    """An engine for a signed-in device. The test chooses how the approval wait sleeps."""

    def make(sleep: Sleep | None = None) -> Engine:
        client = PortalClient(portal.url, token=GOOD_TOKEN)
        return Engine(
            client,
            tmp_path / "cache",
            sleep=sleep if sleep is not None else (lambda _seconds: None),
            poll_seconds=0.0,
        )

    return make


@pytest.fixture
def engine(make_engine: Callable[..., Engine]) -> Engine:
    return make_engine()


@pytest.fixture
def env(portal: FakePortal, tmp_path: Path) -> dict[str, str]:
    """The environment for one person's device. Secrets go to a test-only file store."""
    base = dict(os.environ)
    base.update(
        {
            "GARDEN_HOME": str(tmp_path / "home"),
            "GARDEN_API_URL": portal.url,
            "GARDEN_SECRET_STORE": f"file:{tmp_path / 'secrets.json'}",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return base


@pytest.fixture
def logged_in_env(env: dict[str, str], portal: FakePortal) -> dict[str, str]:
    store_token(env, portal.url, GOOD_TOKEN, device="test-laptop")
    return env


def run_garden(
    env: dict[str, str], *args: str, stdin: str = "", timeout: float = 60
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "garden", *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=ROOT,
        timeout=timeout,
        check=False,
    )


def store_token(
    env: dict[str, str], portal_url: str, token: str, device: str = "test-device"
) -> None:
    """Write a CLI login the way `garden login` would, without running the browser step."""
    old_store = os.environ.get("GARDEN_SECRET_STORE")
    os.environ["GARDEN_SECRET_STORE"] = env["GARDEN_SECRET_STORE"]
    try:
        auth_store_token(token, portal_url=portal_url, device=device)
    finally:
        if old_store is not None:
            os.environ["GARDEN_SECRET_STORE"] = old_store
        else:
            os.environ.pop("GARDEN_SECRET_STORE", None)


def as_json(text: str) -> Any:
    return json.loads(text)


def _read_line(fd: int) -> bytes:
    raw = bytearray()
    while True:
        byte = os.read(fd, 1)
        if not byte:
            break
        raw += byte
        if byte == b"\n":
            break
    return bytes(raw)


def open_sign_in(
    env: dict[str, str], device: str, *flags: str
) -> tuple[subprocess.Popen[str], str, str]:
    """Start `garden login` and read its output up to the sign-in link.

    Returns the process, the lines printed before the link, and the link. The pipe is read one byte
    at a time. A buffered reader can read ahead past the link, and the lines it keeps are then
    lost, because finish_login() reads the pipe itself. Reading the pipe directly keeps every line.
    """
    process = subprocess.Popen(
        [sys.executable, "-m", "garden", "login", "--device", device, *flags],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=ROOT,
    )
    assert process.stdout is not None
    fd = process.stdout.fileno()
    head = ""
    while True:
        line = _read_line(fd).decode("utf-8")
        if not line:
            raise AssertionError(f"`garden login` ended before the link was printed: {head!r}")
        head += line
        if line.startswith("  http"):
            return process, head, line.strip()


def finish_login(process: subprocess.Popen[str], head: str) -> subprocess.CompletedProcess[str]:
    """The rest of `garden login`'s output, with the lines read before the link in front."""
    rest, err = process.communicate(timeout=60)
    return subprocess.CompletedProcess(process.args, process.returncode, head + rest, err)


def sign_in(env: dict[str, str], device: str = "laptop-1") -> subprocess.CompletedProcess[str]:
    """`garden login` with the browser step played: the link is followed as a browser would."""
    process, head, link = open_sign_in(env, device, "--no-open")
    urllib.request.urlopen(link, timeout=30).read()
    return finish_login(process, head)
