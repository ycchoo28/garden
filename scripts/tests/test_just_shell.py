"""The justfile runs recipes through Git for Windows bash, never the WSL launcher on PATH."""

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("just_shell", ROOT / "scripts" / "just_shell.py")
assert SPEC and SPEC.loader
JUST_SHELL = importlib.util.module_from_spec(SPEC)
sys.modules["just_shell"] = JUST_SHELL
SPEC.loader.exec_module(JUST_SHELL)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("bash")
    return path


def test_windows_never_picks_the_path_bash(tmp_path: Path) -> None:
    system32 = tmp_path / "System32"
    _touch(system32 / "bash.exe")
    env = {"PATH": str(system32), "ProgramFiles": str(tmp_path / "missing")}

    with pytest.raises(SystemExit, match="Git for Windows bash was not found"):
        JUST_SHELL.resolve_bash(env, windows=True)


def test_windows_selects_git_bash_even_when_system32_is_first_on_path(tmp_path: Path) -> None:
    system32 = tmp_path / "System32"
    _touch(system32 / "bash.exe")
    git_bash = _touch(tmp_path / "Program Files" / "Git" / "bin" / "bash.exe")
    env = {
        "PATH": f"{system32};{tmp_path / 'other'}",
        "ProgramFiles": str(tmp_path / "Program Files"),
    }

    selected = JUST_SHELL.resolve_bash(env, windows=True)

    assert selected == str(git_bash)
    assert selected != str(system32 / "bash.exe")


def test_windows_prefers_program_files_then_local_install(tmp_path: Path) -> None:
    local = _touch(tmp_path / "Local" / "Programs" / "Git" / "bin" / "bash.exe")
    env = {"LOCALAPPDATA": str(tmp_path / "Local")}

    assert JUST_SHELL.resolve_bash(env, windows=True) == str(local)


def test_portable_git_counts_only_after_its_complete_marker(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    git_home = profile / ".local" / "share" / "garden-tools" / "git"
    _touch(git_home / "bin" / "bash.exe")
    env = {"USERPROFILE": str(profile)}

    # Without the marker, extraction may be half done, so the portable copy is not used.
    with pytest.raises(SystemExit):
        JUST_SHELL.resolve_bash(env, windows=True)

    _touch(git_home / ".complete")
    assert JUST_SHELL.resolve_bash(env, windows=True) == str(git_home / "bin" / "bash.exe")


def test_windows_candidate_order_matches_bootstrap(tmp_path: Path) -> None:
    env = {
        "ProgramFiles": str(tmp_path / "pf"),
        "LOCALAPPDATA": str(tmp_path / "local"),
        "USERPROFILE": str(tmp_path / "profile"),
    }
    candidates = [str(path) for path in JUST_SHELL.git_bash_candidates(env)]

    assert candidates == [
        str(tmp_path / "pf" / "Git" / "bin" / "bash.exe"),
        str(tmp_path / "local" / "Programs" / "Git" / "bin" / "bash.exe"),
        # The portable copy is listed only when its marker exists; this env has none.
    ]
    assert not any("System32" in candidate for candidate in candidates)


def test_posix_uses_bash_from_path(monkeypatch, tmp_path: Path) -> None:
    fake = _touch(tmp_path / "bin" / "bash")
    monkeypatch.setattr(JUST_SHELL.shutil, "which", lambda name: str(fake))

    assert JUST_SHELL.resolve_bash({}, windows=False) == str(fake)


def test_justfile_routes_every_recipe_through_the_runner() -> None:
    text = (ROOT / "justfile").read_text(encoding="utf-8")

    assert '"scripts/just_shell.py", "-cu"]' in text
    assert '"scripts/just_shell.py", "-euo", "pipefail"]' in text
    # A shebang would name a bare bash, which Windows resolves through PATH.
    assert "#!/usr/bin/env bash" not in text
    assert "windows-shell" not in text


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed")
def test_installed_just_accepts_the_justfile() -> None:
    version = subprocess.run(
        ["just", "--version"], capture_output=True, text=True, check=True
    ).stdout.split()[-1]
    if tuple(int(part) for part in version.split(".")) < (1, 33, 0):
        pytest.skip("script-interpreter needs just 1.33 or later")
    subprocess.run(["just", "--summary"], cwd=ROOT, capture_output=True, check=True)
