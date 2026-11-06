"""dsh runtime orchestration: configuration, fallback patch handling, and node execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from garden.auth import require_session
from garden.dsh.launcher import entry_point, with_default_profile
from garden.dsh.patch import PATCH_NAME, TOKEN_ENV, default_model, write_patch
from garden.engine import PortalClient
from garden.shared import GardenError, Settings, load_settings


def configure(
    *,
    settings: Settings | None = None,
    token: str | None = None,
    stderr: TextIO | None = None,
) -> Path:
    """Resolve portal settings, query the default model, and write the dsh patch."""
    active_settings = settings or load_settings()
    resolved_token = token if token is not None else require_session(active_settings).token
    try:
        model = default_model(PortalClient(active_settings.portal_url, token=resolved_token))
    except GardenError as error:
        patch_file = active_settings.dsh_home / PATCH_NAME
        if patch_file.is_file() and error.status is None:
            print(
                f"warning: dsh keeps its last model, since the portal did not answer "
                f"({error.title}).",
                file=stderr or sys.stderr,
            )
            return patch_file
        raise
    return write_patch(
        active_settings.dsh_home,
        portal_url=active_settings.portal_url,
        model=model,
        settings=active_settings,
    )


def run(
    dsh_args: Sequence[str] = (),
    *,
    settings: Settings | None = None,
    token: str | None = None,
    runner: Callable[..., subprocess.CompletedProcess[Any]] = subprocess.run,
    stderr: TextIO | None = None,
) -> int:
    """Run dsh with the portal model in its environment.

    1. Resolves settings and the active login token.
    2. Validates Node.js is available on PATH.
    3. Validates the dsh entry point exists.
    4. Refreshes the patch from the portal, keeping the existing patch if unreachable.
    5. Sets DSH_HOME and the token variable in the child environment.
    6. Executes dsh via runner and returns its exit code.
    """
    active_settings = settings or load_settings()
    resolved_token = token if token is not None else require_session(active_settings).token

    if shutil.which("node") is None:
        raise GardenError(
            title="Node.js is not installed",
            detail="dsh needs Node.js 22 or newer on your PATH.",
            status=0,
            fix="Install Node.js 22 or newer, then run `garden dsh` again.",
        )

    entry = entry_point(active_settings.dsh_home)
    if not entry.is_file():
        home_entry = entry_point(active_settings.home)
        if home_entry.is_file():
            entry = home_entry
    if not entry.is_file():
        raise GardenError(
            title="dsh is not installed",
            detail=f"Run the Garden installer to place dsh under {active_settings.dsh_home}.",
            status=0,
            fix="Run the installer again.",
        )

    configure(settings=active_settings, token=resolved_token, stderr=stderr)

    env = dict(os.environ)
    env["DSH_HOME"] = str(active_settings.dsh_home)
    env[TOKEN_ENV] = resolved_token

    completed = runner(["node", str(entry), *with_default_profile(dsh_args)], env=env, check=False)
    return int(completed.returncode)
