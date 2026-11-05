"""The installer, run unattended against the fake portal (`just agent-install-test`).

The test builds a zipapp of the garden package, serves it with the manifest the installer reads, and
checks that the launchers, the signed-in CLI, the unpacked dsh and the dsh patch all came out right.
The zipapp here is built from the source alone, under the test interpreter, which already has the
runtime dependencies. The real build, which vendors them for Windows too, is `just agent-build`, and
it needs the package index. The model is the portal's (ADR 0024): the installer takes no model
setting, and no key or token is written to disk. The dsh bundle here is a stand-in: the real one is
built by scripts/build_dsh_bundle.py.
"""

from __future__ import annotations

import io
import json
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import zipapp
from pathlib import Path

import yaml
from conftest import ROOT, as_json, run_garden
from fake_portal import GOOD_TOKEN, FakePortal

from garden import dsh

INSTALLER = ROOT / "install" / "install.sh"
INSTALLER_WINDOWS = ROOT / "install" / "install.ps1"
DSH_TEMPLATE = ROOT / "install" / "dsh" / "cordis.patch.yml"
# Names of the model settings the installers could take. None may come back (ADR 0024).
RETIRED_MODEL_SETTINGS = ("GARDEN_MODEL_", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "{{MODEL_KEY")
PORTAL_NAME = "Marcus Lee"


def _stand_in_dsh_bundle() -> bytes:
    """A tar.gz shaped like the real bundle: node_modules/@deepseek-ai/dsh/lib/bin.js at its top."""
    buffer = io.BytesIO()
    script = b"console.log('dsh stand-in');\n"
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("node_modules/@deepseek-ai/dsh/lib/bin.js")
        info.size = len(script)
        archive.addfile(info, io.BytesIO(script))
    return buffer.getvalue()


def _build_garden_pyz(target: Path) -> Path:
    """A zipapp of the garden package, with the same entry script as the real build."""
    staging = target.parent / "staging"
    shutil.copytree(
        ROOT / "src" / "garden", staging / "garden", ignore=shutil.ignore_patterns("__pycache__")
    )
    (staging / "__main__.py").write_text(
        "import sys\n\nfrom garden.cli import main\n\nsys.exit(main())\n", encoding="utf-8"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    zipapp.create_archive(staging, target=target, compressed=True)
    return target


def _serve_downloads(portal: FakePortal, pyz: Path) -> None:
    portal.downloads["garden.pyz"] = pyz.read_bytes()
    portal.downloads["dsh-bundle.tar.gz"] = _stand_in_dsh_bundle()
    portal.downloads["dsh-config.yml"] = DSH_TEMPLATE.read_bytes()


_STAGE_LINE = re.compile(r"^\[(\d+)/(\d+)\] (.+)$", re.MULTILINE)


def _assert_numbered_stages(stderr: str, *, total: int) -> None:
    """Every stage starts as [n/total], the numbers run in order from 1, and each one is Done."""
    lines = _STAGE_LINE.findall(stderr)
    starts = [
        (int(number), int(of), text)
        for number, of, text in lines
        if not text.startswith(("Done: ", "Failed: "))
    ]
    assert [number for number, _, _ in starts] == list(range(1, total + 1)), stderr
    assert {of for _, of, _ in starts} == {total}, stderr
    assert starts[0][2] == "Fetching download manifest..."
    done = [int(number) for number, _, text in lines if text.startswith("Done: ")]
    assert done == list(range(1, total + 1)), stderr
    assert not [text for _, _, text in lines if text.startswith("Failed: ")], stderr


def test_a_failed_stage_is_named_with_its_number(env: dict[str, str], tmp_path: Path) -> None:
    # Nothing listens on this port, so the first stage (the manifest) fails.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    failed = subprocess.run(
        ["bash", str(INSTALLER)],
        env={
            **env,
            "GARDEN_API_URL": f"http://127.0.0.1:{closed_port}",
            "GARDEN_PYTHON": sys.executable,
            "GARDEN_BIN_DIR": str(tmp_path / "bin"),
            "GARDEN_HOME": str(tmp_path / "home"),
            "GARDEN_DSH_HOME": str(tmp_path / "dsh"),
            "GARDEN_INSTALL_TEST": "1",
        },
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert failed.returncode != 0
    assert "[1/8] Fetching download manifest..." in failed.stderr
    assert "[1/8] Failed: Fetching download manifest..." in failed.stderr
    assert "[2/8]" not in failed.stderr


def test_installer_runs_unattended_and_the_launchers_and_dsh_patch_work(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    _serve_downloads(portal, _build_garden_pyz(tmp_path / "served" / "garden.pyz"))

    bin_dir = tmp_path / "bin"
    dsh_home = tmp_path / "dsh"
    install_env = {
        **env,
        "GARDEN_API_URL": portal.url,
        "GARDEN_PYTHON": sys.executable,
        "GARDEN_BIN_DIR": str(bin_dir),
        "GARDEN_DSH_HOME": str(dsh_home),
        "GARDEN_INSTALL_TEST": "1",
    }
    installed = subprocess.run(
        ["bash", str(INSTALLER)],
        env=install_env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    # The installer reports the smoke check and emits no uv warning or force suggestion.
    assert "Ready:" in installed.stdout
    assert "Approve the sign-in in your browser: http" in installed.stdout
    _assert_numbered_stages(installed.stderr, total=8)
    assert re.search(
        r"\[2/8\] Downloading garden\.pyz \([\d.]+ (?:KB|MB|bytes)\)\.\.\.", installed.stderr
    )
    assert re.search(r"\[5/8\] Done: Downloading dsh-bundle\.tar\.gz", installed.stderr)
    # Piped output (as here, and in CI) has the step lines only: no meter and no escape codes.
    assert "% Total" not in installed.stderr
    assert "\x1b" not in installed.stderr
    assert "--force" not in installed.stdout + installed.stderr

    launcher = bin_dir / "garden"
    assert launcher.exists()
    who = subprocess.run(
        [str(launcher), "whoami", "--json"],
        env=install_env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert who.returncode == 0, who.stderr
    assert as_json(who.stdout)["name"] == PORTAL_NAME

    for skill in ("garden-basics", "garden-budgets", "garden-claims", "garden-knowledge"):
        assert (tmp_path / "home" / "skills" / skill / "SKILL.md").exists()

    # dsh's launcher is written by garden: it runs `garden dsh`, which reads the login at start.
    dsh_launcher = (bin_dir / "dsh").read_text(encoding="utf-8")
    assert f'exec {launcher} dsh "$@"' in dsh_launcher
    assert "DSH_HOME" not in dsh_launcher
    dsh_entry = tmp_path / "home" / "dsh" / "node_modules" / "@deepseek-ai" / "dsh"
    assert (dsh_entry / "lib" / "bin.js").exists()

    patch_text = (dsh_home / "cordis.patch.yml").read_text(encoding="utf-8")
    documents = yaml.safe_load(patch_text)
    rows = documents[0]["insert"]
    mcp = next(row for row in rows if row["name"] == "@deepseek-ai/dsh-mcp-client")
    assert mcp["config"]["serverName"] == "garden"
    assert mcp["config"]["command"] == str(launcher)
    assert mcp["config"]["args"] == ["mcp"]
    model = next(row for row in rows if row["name"] == "@deepseek-ai/dsh-llm-pi-ai")
    provider = model["config"]["providers"]["garden-portal"]
    assert provider["apiKeyEnv"] == "GARDEN_AGENT_MODEL_TOKEN"
    assert provider["baseURL"] == f"{portal.url}/api/agent/model/v1"
    assert provider["models"][0]["id"] == "deepseek-v4-flash"
    assert documents[1]["id"] == "agent-default-model"
    assert documents[1]["config"] == {"provider": "garden-portal", "model": "deepseek-v4-flash"}
    assert re.search(r"\{\{[A-Z_]+\}\}", patch_text) is None


def test_the_built_zipapp_refuses_to_run_without_a_portal_and_says_why(
    tmp_path: Path, env: dict[str, str]
) -> None:
    pyz = _build_garden_pyz(tmp_path / "garden.pyz")
    bare = {key: value for key, value in env.items() if key != "GARDEN_API_URL"}
    result = subprocess.run(
        [sys.executable, str(pyz), "whoami"],
        env=bare,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1
    assert "No portal address" in result.stderr
    assert run_garden(bare, "--version").returncode == 0


def test_the_installers_take_no_model_setting_and_write_no_key() -> None:
    """The model is the portal's: no installer names a model, an endpoint or a key (ADR 0024)."""
    for script in (INSTALLER, INSTALLER_WINDOWS):
        text = script.read_text(encoding="utf-8")
        for retired in RETIRED_MODEL_SETTINGS:
            assert retired not in text, f"{script.name} still has {retired}"
    template = DSH_TEMPLATE.read_text(encoding="utf-8")
    for retired in ("GARDEN_MODEL_", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY"):
        assert retired not in template, f"the template still has {retired}"
    assert "{{MODEL_BASE_URL}}" in template
    assert "apiKeyEnv" in template


def test_the_installer_run_leaves_the_token_only_in_the_keychain_store(
    portal: FakePortal, env: dict[str, str], tmp_path: Path
) -> None:
    """Run the whole installer, then look for the person's token in every file it wrote, but the
    keychain's own store: the token must be nowhere else."""
    _serve_downloads(portal, _build_garden_pyz(tmp_path / "served" / "garden.pyz"))
    install_env = {
        **env,
        "GARDEN_PYTHON": sys.executable,
        "GARDEN_BIN_DIR": str(tmp_path / "bin"),
        "GARDEN_DSH_HOME": str(tmp_path / "dsh"),
        "GARDEN_INSTALL_TEST": "1",
    }
    installed = subprocess.run(
        ["bash", str(INSTALLER)], env=install_env, capture_output=True, text=True, timeout=300
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    store = tmp_path / "secrets.json"
    assert store.exists()
    token = json.loads(json.loads(store.read_text(encoding="utf-8"))[portal.url])["token"]
    assert token == GOOD_TOKEN

    written = [path for path in tmp_path.rglob("*") if path.is_file() and path != store]
    assert written, "the installer wrote files"
    for path in written:
        assert token not in path.read_text(encoding="utf-8", errors="ignore"), path
    config = json.loads((tmp_path / "home" / "config.json").read_text(encoding="utf-8"))
    assert set(config) == {"portal_url", "garden_command", "dsh_home"}


def test_the_installers_stop_uv_writing_python_shims_into_the_launcher_folder() -> None:
    """uv warns about an existing python shim there, so both installers pass --no-bin to it."""
    assert "python install --no-bin --quiet" in INSTALLER.read_text(encoding="utf-8")


def test_the_smoke_check_counts_the_tools_the_person_can_list() -> None:
    """The smoke check reads `garden tools --json` and counts the rows it gets back."""
    assert "print(len(json.load(sys.stdin)))" in INSTALLER.read_text(encoding="utf-8")
    assert "$toolCount = @($listed).Count" in INSTALLER_WINDOWS.read_text(encoding="utf-8")


def test_sign_in_is_the_last_step_so_a_failed_sign_in_leaves_everything_installed() -> None:
    """Both installers install garden, dsh, the skills and the patch before they sign in."""
    shell = INSTALLER.read_text(encoding="utf-8")
    assert shell.index("fetch_artifact dsh-config.yml") < shell.index("if ! sign_in")
    assert (
        shell.index("skills install") < shell.index("if ! sign_in") < shell.index("# A smoke check")
    )
    script = INSTALLER_WINDOWS.read_text(encoding="utf-8")
    assert "if (-not $env:GARDEN_INSTALL_TEST) { Add-UserPath $binDir }" in script
    assert script.index("Add-UserPath $binDir") < script.index("Invoke-SignIn $python $pyz")
    assert script.index("Invoke-SignIn $python $pyz") < script.index("# A smoke check")
    assert script.index("'skills', 'install'") < script.index("Invoke-SignIn $python $pyz")


def test_the_windows_installer_fetches_its_bundle_and_the_patch_has_no_placeholder_left() -> None:
    """A text check, because CI has no PowerShell. It keeps install.ps1 in step with install.sh.

    The Windows installer must fetch the Windows dsh bundle. The patch is filled by garden, so no
    `{{...}}` reaches the patch on a laptop.
    """
    script = INSTALLER_WINDOWS.read_text(encoding="utf-8")
    assert "Get-Artifact 'dsh-bundle-windows-x64.tar.gz'" in script
    # Without this read, PowerShell 5.1 reports a null exit code for the login and fails.
    assert "$null = $login.Handle" in script
    assert "--no-bin" in script
    # Downloads stream through HttpWebRequest with their own meter, not Invoke-WebRequest's bar.
    assert "HttpWebRequest" in script
    assert "Invoke-WebRequest -UseBasicParsing -Uri ($portal + $artifact.url)" not in script
    assert 'Start-Step "Downloading $name ($(Format-Size $size))..."' in script
    # The meter is written only to a console, and its progress preference is restored afterwards.
    assert "IsOutputRedirected" in script
    assert "$savedProgress" in script
    # The browser UI's shortcuts: a start and a stop shortcut in the Start Menu folder, and a start
    # one on the desktop. Each runs PowerShell hidden, so no console window shows.
    assert "Install-WebShortcuts $launcher $gardenHome" in script
    assert "'Garden Agent.lnk'" in script
    assert "'Stop Garden Agent.lnk'" in script
    assert "-WindowStyle Hidden -File" in script
    # The shortcuts start wscript, which runs PowerShell in a hidden window through a small script.
    assert "//B //Nologo" in script
    assert "WScript.Shell" in script
    assert "$wscript" in script
    assert "--no-open" in script
    assert "$webPort = 8765" in script
    # dsh web prints its address with its access token. The start script opens that address.
    assert "dsh web: (http://" in script
    # The shortcut's start script pins the home it was installed into, never another home.
    assert "$env:GARDEN_HOME = '__GARDENFOLDER__'" in script
    assert "Replace('__GARDENFOLDER__'" in script
    # The model is the portal's (ADR 0024): garden fills the patch on each laptop.
    template = DSH_TEMPLATE.read_text(encoding="utf-8")
    assert re.findall(r"\{\{[A-Z_]+\}\}", template), (
        "the dsh patch template has no placeholders to check"
    )
    rendered = dsh.render_patch(
        template,
        garden_command="/opt/garden",
        garden_args=("mcp",),
        base_url="https://portal.example/api/agent/model/v1",
        model="model-id",
    )
    assert not re.findall(r"\{\{[A-Z_]+\}\}", rendered), "a placeholder is left in the patch"
