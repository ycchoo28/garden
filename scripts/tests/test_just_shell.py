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


def test_justfile_sets_the_runner_only_for_windows() -> None:
    text = (ROOT / "justfile").read_text(encoding="utf-8")

    # windows-shell is read only on Windows, and older just versions parse it too.
    assert (
        'set windows-shell := ["uv", "run", "--no-project", "--python", "3.12", "python", ' in text
    )
    assert '"scripts/just_shell.py", "-cu"]' in text
    # Linux and macOS keep the default shell, and nothing newer than just 1.21 is used.
    assert "\nset shell" not in text
    assert "script-interpreter" not in text
    assert "[windows]\nset " not in text


def test_every_bash_shebang_recipe_is_tagged_unix() -> None:
    lines = (ROOT / "justfile").read_text(encoding="utf-8").split("\n")
    shebangs = [i for i, line in enumerate(lines) if line == "    #!/usr/bin/env bash"]
    assert shebangs, "expected bash recipes"
    for index in shebangs:
        header = index - 1
        while lines[header].startswith(("#", " ")) or lines[header] == "":
            header -= 1
        assert lines[header - 1] == "[unix]", lines[header]


def test_windows_has_a_recipe_for_each_demo_command() -> None:
    lines = (ROOT / "justfile").read_text(encoding="utf-8").split("\n")
    windows_recipes = {
        lines[i + 1].split(":")[0].split()[0]
        for i, line in enumerate(lines)
        if line == "[windows]" and i + 1 < len(lines)
    }
    assert {"setup", "demo", "demo-web", "demo-clean", "demo-uninstall"} <= windows_recipes


def test_runner_runs_a_recipe_line_with_bash() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "just_shell.py"), "-cu", "x=ok; echo $x"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "ok"


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed")
def test_installed_just_parses_the_justfile() -> None:
    # Bootstrap reuses any just already on PATH, so the justfile must parse with older versions too.
    subprocess.run(["just", "--summary"], cwd=ROOT, capture_output=True, check=True)
