"""dsh entry point, profile selection, and launcher generation."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

# The profile dsh boots when the person names none. dsh has no default-profile setting and refuses
# to start without a profile, so `garden dsh` passes this one. `headless` is the shipped profile
# that the Garden agent runs dsh with. The home patch applies to every profile.
DEFAULT_PROFILE = "headless"
# Flags that ask dsh for its own help or version. dsh answers them without a profile.
_HELP_AND_VERSION_FLAGS = frozenset({"-h", "--help", "-V", "--version"})


def entry_point(home: Path) -> Path:
    """dsh's script, as the installer unpacks the bundle under the garden home."""
    cand1 = home / "dsh" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    if cand1.is_file():
        return cand1
    cand2 = home / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    if cand2.is_file():
        return cand2
    return cand1


def with_default_profile(dsh_args: Sequence[str]) -> list[str]:
    """dsh's arguments, with the default profile added when the person names none.

    dsh boots the profile named by `--profile`, or by a first argument such as `dsh headless`.
    Plain `dsh`, and `dsh` with flags only, get the default. Help, version and `dsh plugin ...` are
    left as they are, and so is any call that already names a profile.
    """
    if not dsh_args:
        return ["--profile", DEFAULT_PROFILE]
    first = dsh_args[0]
    if first == "plugin" or not first.startswith("-"):
        return list(dsh_args)
    if any(arg == "--profile" or arg.startswith("--profile=") for arg in dsh_args):
        return list(dsh_args)
    if any(arg in _HELP_AND_VERSION_FLAGS for arg in dsh_args):
        return list(dsh_args)
    return ["--profile", DEFAULT_PROFILE, *dsh_args]


def launcher_text(garden_command: str, *, windows: bool) -> str:
    """The `dsh` launcher: it runs `garden dsh`, which reads the login and starts dsh."""
    if windows:
        return f'@echo off\r\n"{garden_command}" dsh %*\r\n'
    return f'#!/usr/bin/env bash\nexec {shlex.quote(garden_command)} dsh "$@"\n'


def write_launcher(target: Path, *, garden_command: str) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    windows = target.suffix.lower() in {".cmd", ".bat"}
    target.write_bytes(launcher_text(garden_command, windows=windows).encode("utf-8"))
    target.chmod(0o755)
    return target
