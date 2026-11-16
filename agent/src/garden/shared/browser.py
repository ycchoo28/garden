"""Open a user-facing link, using the Windows browser when this process runs in WSL."""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import webbrowser
from collections.abc import Callable, Mapping
from pathlib import Path


def open_link(url: str, opener: Callable[[str], bool] | None = None) -> bool:
    """Ask the browser to open the link. Return false when no opener succeeds."""
    open_with = opener if opener is not None else webbrowser.open

    def attempt() -> bool:
        if opener is None and is_wsl() and "BROWSER" not in os.environ and _open_in_windows(url):
            return True
        return _attempt(url, open_with)

    try:
        saved = [os.dup(fd) for fd in (1, 2)]
    except OSError:
        return attempt()
    null = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(null, 1)
        os.dup2(null, 2)
        return attempt()
    finally:
        os.dup2(saved[0], 1)
        os.dup2(saved[1], 2)
        for fd in (*saved, null):
            os.close(fd)


def is_wsl(environ: Mapping[str, str] | None = None, osrelease: str | None = None) -> bool:
    values = os.environ if environ is None else environ
    if values.get("WSL_DISTRO_NAME") or values.get("WSL_INTEROP"):
        return True
    if osrelease is None:
        try:
            osrelease = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
        except OSError:
            return False
    return "microsoft" in osrelease.casefold()


def _open_in_windows(url: str) -> bool:
    commands: list[list[str]] = []
    wslview = shutil.which("wslview")
    if wslview:
        commands.append([wslview, url])
    explorer = shutil.which("explorer.exe")
    if explorer:
        commands.append([explorer, url])
    powershell = shutil.which("powershell.exe")
    if powershell:
        quoted_url = url.replace("'", "''")
        script = f"Start-Process -FilePath '{quoted_url}'"
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        commands.append([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded])

    for command in commands:
        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0:
            return True
    return False


def _attempt(url: str, open_with: Callable[[str], bool]) -> bool:
    try:
        return bool(open_with(url))
    except webbrowser.Error:
        return False
