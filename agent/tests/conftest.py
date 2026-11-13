"""Shared fixtures: a fake portal on a loopback port, and an engine that talks to it."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from garden.engine import Engine, PortalClient
from tests.shared.fake_portal import GOOD_TOKEN, FakePortal
from tests.shared.garden_cli import store_token

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
