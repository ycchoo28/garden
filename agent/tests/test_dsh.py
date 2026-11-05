"""dsh with the portal's model (ADR 0024): the launcher, the patch, and the token's path.

The token is read from the keychain when `garden dsh` starts, and goes only into dsh's process
environment. A stand-in `node` shows what dsh was given. Nothing under the home or dsh's folder
may hold the token afterwards.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml
from conftest import ROOT, run_garden, store_token
from fake_portal import GOOD_TOKEN, FakePortal

from garden import dsh

DSH_TEMPLATE = ROOT / "install" / "dsh" / "cordis.patch.yml"


def _dsh_home(env: dict[str, str], tmp_path: Path) -> Path:
    """Make the folders dsh and garden expect, and a stand-in `node` on PATH."""
    home = Path(env["GARDEN_HOME"])
    entry = home / "dsh" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("// stand-in for dsh\n", encoding="utf-8")
    (home / "dsh-config.yml").write_bytes(DSH_TEMPLATE.read_bytes())
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    node = bin_dir / "node"
    # Prints what dsh receives: its arguments, DSH_HOME and the token variable.
    script = (
        "#!/bin/sh\n"
        'echo "args: $*"\n'
        'echo "DSH_HOME=$DSH_HOME"\n'
        'echo "TOKEN=$GARDEN_AGENT_MODEL_TOKEN"\n'
    )
    node.write_text(script, encoding="utf-8")
    node.chmod(node.stat().st_mode | stat.S_IEXEC)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    return home


def _env(env: dict[str, str], tmp_path: Path) -> dict[str, str]:
    result = {**env, "GARDEN_DSH_HOME": str(tmp_path / "dsh")}
    Path(env["GARDEN_HOME"]).mkdir(parents=True, exist_ok=True)
    config = {"portal_url": env["GARDEN_API_URL"], "garden_command": "/opt/garden/bin/garden"}
    (Path(env["GARDEN_HOME"]) / "config.json").write_text(json.dumps(config), encoding="utf-8")
    return result


def _files_under(*roots: Path) -> list[Path]:
    return [path for root in roots if root.exists() for path in root.rglob("*") if path.is_file()]


def test_garden_dsh_starts_dsh_with_the_login_in_its_environment_only(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    run_env = _env(env, tmp_path)
    home = _dsh_home(run_env, tmp_path)

    result = run_garden(run_env, "dsh", "--model-check", "hello")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    # A flag names no profile, so dsh is given the default one ahead of the arguments.
    assert any(
        line.endswith("bin.js --profile headless --model-check hello")
        for line in lines
        if line.startswith("args")
    )
    assert f"DSH_HOME={tmp_path / 'dsh'}" in lines
    assert f"TOKEN={GOOD_TOKEN}" in lines
    # The token is in the keychain store and in dsh's environment, and nowhere else on disk.
    for path in _files_under(home, tmp_path / "dsh", tmp_path / "fake-bin"):
        assert GOOD_TOKEN not in path.read_text(encoding="utf-8", errors="ignore"), path
    assert (tmp_path / "dsh" / "cordis.patch.yml").exists()


def test_the_patch_names_the_gateway_and_the_portals_default_model_but_no_token(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    run_env = _env(env, tmp_path)
    _dsh_home(run_env, tmp_path)

    result = run_garden(run_env, "dsh-config")

    assert result.returncode == 0, result.stderr
    text = (tmp_path / "dsh" / "cordis.patch.yml").read_text(encoding="utf-8")
    assert GOOD_TOKEN not in text
    documents = yaml.safe_load(text)
    rows = documents[0]["insert"]
    mcp = next(row for row in rows if row["name"] == "@deepseek-ai/dsh-mcp-client")
    assert mcp["config"]["command"] == "/opt/garden/bin/garden"
    provider = next(row for row in rows if row["name"] == "@deepseek-ai/dsh-llm-pi-ai")
    assert list(provider["config"]["providers"]) == ["garden-portal"]
    gateway = provider["config"]["providers"]["garden-portal"]
    assert gateway["baseURL"] == f"{portal.url}/api/agent/model/v1"
    assert gateway["apiKeyEnv"] == "GARDEN_AGENT_MODEL_TOKEN"
    assert gateway["models"][0]["id"] == "deepseek-v4-flash"
    default = documents[1]
    assert default["id"] == "agent-default-model"
    assert default["config"] == {"provider": "garden-portal", "model": "deepseek-v4-flash"}


def test_the_patch_follows_the_default_model_the_portal_names_now(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    run_env = _env(env, tmp_path)
    _dsh_home(run_env, tmp_path)
    assert run_garden(run_env, "dsh-config").returncode == 0
    portal.model_default = "vendor/next-model:free"

    result = run_garden(run_env, "dsh-config")

    assert result.returncode == 0, result.stderr
    assert "vendor/next-model:free" in (tmp_path / "dsh" / "cordis.patch.yml").read_text(
        encoding="utf-8"
    )


def test_when_the_portal_cannot_be_reached_dsh_keeps_its_last_patch_and_says_so(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    run_env = _env(env, tmp_path)
    _dsh_home(run_env, tmp_path)
    assert run_garden(run_env, "dsh-config").returncode == 0
    patch = tmp_path / "dsh" / "cordis.patch.yml"
    before = patch.read_text(encoding="utf-8")
    portal.stop()

    result = run_garden(run_env, "dsh", "hello")

    assert result.returncode == 0, result.stderr
    assert "keeps its last model" in result.stderr
    assert patch.read_text(encoding="utf-8") == before
    assert "bin.js hello" in result.stdout


def test_a_portal_refusal_stops_dsh_instead_of_running_it_on_an_old_patch(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, "not-a-login")
    run_env = _env(env, tmp_path)
    _dsh_home(run_env, tmp_path)

    result = run_garden(run_env, "dsh", "hello")

    assert result.returncode == 1
    assert "args:" not in result.stdout
    assert "Run `garden login`" in result.stderr


def test_dsh_needs_a_login(portal: FakePortal, env: dict[str, str], tmp_path: Path) -> None:
    run_env = _env(env, tmp_path)
    _dsh_home(run_env, tmp_path)

    result = run_garden(run_env, "dsh")

    assert result.returncode == 1
    assert "Not signed in" in result.stderr
    assert not (tmp_path / "dsh" / "cordis.patch.yml").exists()


def test_the_dsh_launcher_is_written_by_garden_and_runs_garden_dsh(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    run_env = _env(env, tmp_path)
    target = tmp_path / "bin" / "dsh"

    result = run_garden(
        run_env, "dsh-launcher", "--to", str(target), "--garden", "/opt/garden/garden"
    )

    assert result.returncode == 0, result.stderr
    text = target.read_text(encoding="utf-8")
    assert text == '#!/usr/bin/env bash\nexec /opt/garden/garden dsh "$@"\n'
    assert os.access(target, os.X_OK)


def test_the_dsh_launcher_for_windows_is_a_cmd_file() -> None:
    text = dsh.launcher_text("C:\\Program Files\\garden\\garden.exe", windows=True)

    assert text == '@echo off\r\n"C:\\Program Files\\garden\\garden.exe" dsh %*\r\n'


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ([], ["--profile", "headless"]),
        (["--model-check", "hello"], ["--profile", "headless", "--model-check", "hello"]),
        (["--patch", "extra.yml"], ["--profile", "headless", "--patch", "extra.yml"]),
        (["headless", "--json"], ["headless", "--json"]),
        (["web"], ["web"]),
        (["--profile", "web", "-x"], ["--profile", "web", "-x"]),
        (["--profile=web"], ["--profile=web"]),
        (["-h"], ["-h"]),
        (["--version"], ["--version"]),
        (["plugin", "list"], ["plugin", "list"]),
    ],
)
def test_plain_dsh_boots_the_default_profile_unless_the_person_names_one(
    given: list[str], expected: list[str]
) -> None:
    assert dsh.with_default_profile(given) == expected


def test_garden_dsh_with_no_arguments_passes_the_default_profile_to_dsh(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    run_env = _env(env, tmp_path)
    _dsh_home(run_env, tmp_path)

    plain = run_garden(run_env, "dsh")
    named = run_garden(run_env, "dsh", "headless", "--json")

    assert plain.returncode == 0, plain.stderr
    assert any(
        line.endswith("bin.js --profile headless")
        for line in plain.stdout.splitlines()
        if line.startswith("args")
    )
    assert named.returncode == 0, named.stderr
    assert any(
        line.endswith("bin.js headless --json")
        for line in named.stdout.splitlines()
        if line.startswith("args")
    )


@pytest.mark.parametrize("named_default", ['bad"model\n  x: y', None])
def test_a_model_name_that_could_break_the_patch_or_is_missing_is_refused(
    portal: FakePortal, env: dict[str, str], tmp_path: Path, named_default: str | None
) -> None:
    store_token(env, portal.url, GOOD_TOKEN)
    run_env = _env(env, tmp_path)
    _dsh_home(run_env, tmp_path)
    portal.model_default = named_default

    result = run_garden(run_env, "dsh-config")

    assert result.returncode == 1
    assert "cannot use" in result.stderr
    assert not (tmp_path / "dsh" / "cordis.patch.yml").exists()
