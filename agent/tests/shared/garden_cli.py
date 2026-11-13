"""The garden command as a test drives it: run it, store a login, and follow the sign-in link."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.request
from typing import Any

from garden.auth import store_token as auth_store_token
from tests.shared.paths import AGENT_ROOT as ROOT


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
