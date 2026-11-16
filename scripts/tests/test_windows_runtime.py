"""Native Windows startup must reach Psycopg's connection attempt, not reject its event loop."""

import socket
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows event loop")
def test_backend_entry_points_can_use_psycopg_async():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    source = f"""
import asyncio
import app
import psycopg
from uvicorn import Config

async def check():
    assert isinstance(asyncio.get_running_loop(), asyncio.SelectorEventLoop)
    try:
        await psycopg.AsyncConnection.connect('host=127.0.0.1 port={port} connect_timeout=1')
    except psycopg.OperationalError:
        pass
    else:
        raise AssertionError('The test port unexpectedly accepted a database connection')

asyncio.run(check())
factory = Config('app.main:app', loop='asyncio:SelectorEventLoop').get_loop_factory()
loop = factory()
assert isinstance(loop, asyncio.SelectorEventLoop)
loop.close()
"""
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=Path(__file__).resolve().parents[2] / "backend",
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
