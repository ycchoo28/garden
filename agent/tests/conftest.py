"""Shared fixtures: a fake portal on a loopback port, and an engine that talks to it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fake_portal import GOOD_TOKEN, FakePortal

from garden.engine import Engine
from garden.portal import PortalClient

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
    path = Path(env["GARDEN_SECRET_STORE"].removeprefix("file:"))
    data: dict[str, str] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    data[portal_url] = json.dumps({"token": token, "device": device})
    path.write_text(json.dumps(data), encoding="utf-8")


def as_json(text: str) -> Any:
    return json.loads(text)
