"""Run just recipe lines and [script] recipes with bash on every platform.

The justfile sends each recipe line, and each [script] recipe body, through this file. On Windows
a bare `bash` on PATH can be the WSL launcher, C:\\Windows\\System32\\bash.exe, which mangles
Windows paths. So on Windows this never searches PATH: it uses Git for Windows bash from its
install folders, or the PortableGit folder that scripts/bootstrap.ps1 installs.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

GIT_BASH_NOT_FOUND = (
    "Git for Windows bash was not found in Program Files, LocalAppData or the Garden tools folder. "
    "Install Git for Windows, or run scripts\\bootstrap.ps1 to install the portable copy."
)


def git_bash_candidates(env: Mapping[str, str]) -> list[Path]:
    """Git bash locations, in the order that scripts/bootstrap.ps1 checks them."""
    candidates = []
    program_files = env.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "Git" / "bin" / "bash.exe")
    local_app_data = env.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Git" / "bin" / "bash.exe")
    profile = env.get("USERPROFILE")
    if profile:
        git_home = Path(profile) / ".local" / "share" / "garden-tools" / "git"
        # PortableGit counts only once the bootstrap has finished extracting it.
        if (git_home / ".complete").is_file():
            candidates.append(git_home / "bin" / "bash.exe")
    return candidates


def resolve_bash(env: Mapping[str, str] = os.environ, *, windows: bool = os.name == "nt") -> str:
    """Return the bash to run recipes with. On Windows PATH is never searched."""
    if not windows:
        found = shutil.which("bash")
        if found is None:
            raise SystemExit(
                "bash was not found on PATH; install bash to run the justfile recipes."
            )
        return found
    for candidate in git_bash_candidates(env):
        if candidate.is_file():
            return str(candidate)
    raise SystemExit(GIT_BASH_NOT_FOUND)


def main(argv: list[str]) -> int:
    return subprocess.call([resolve_bash(), *argv])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
