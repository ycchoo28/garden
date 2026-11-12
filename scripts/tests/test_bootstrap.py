"""Verified bootstrap downloads at the process boundary, with no real tool installation."""

import hashlib
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("bootstrap", ROOT / "scripts/bootstrap.py")
assert SPEC and SPEC.loader
BOOTSTRAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BOOTSTRAP)


@pytest.mark.parametrize("valid", [True, False])
@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX executable fixtures; PowerShell bootstrap checked on win-test",
)
def test_downloads_are_checked_before_any_executable_is_installed(tmp_path, valid):
    script = tmp_path / "bootstrap.py"
    shutil.copy2(ROOT / "scripts/bootstrap.py", script)
    shutil.copy2(ROOT / "scripts/install_manifest.py", script.with_name("install_manifest.py"))
    archive = tmp_path / "tool.zip"
    # An executable that can report its version without relying on PATH.
    with zipfile.ZipFile(archive, "w") as package:
        for tool in ("just", "bun"):
            package.writestr(tool, f"#!{sys.executable}\nprint('26.13.0')\n")
        package.writestr("node-v26.13.0-linux-x64/include/node/", "")
        package.writestr(
            "node-v26.13.0-linux-x64/bin/node",
            f"#!{sys.executable}\nprint('26.13.0')\n",
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest() if valid else "0" * 64
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    pins = {
        f"{platform.system().lower()}-{arch}": {
            tool: {"url": archive.as_uri(), "sha256": digest} for tool in ("just", "bun", "node")
        }
    }
    script.with_name("bootstrap-tools.json").write_text(json.dumps(pins))
    home = tmp_path / "home"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, str(script)],
        env={**os.environ, "HOME": str(home), "PATH": str(tmp_path / "empty")},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert (result.returncode == 0) == valid, result.stderr
    assert (home / ".local/bin/just").exists() == valid
    if not valid:
        assert "checksum mismatch" in result.stderr
    else:
        manifest = json.loads((home / ".local/share/garden/install-manifest.json").read_text())
        assert str(home / ".local/bin/just") in {entry["path"] for entry in manifest["artifacts"]}


@pytest.mark.skipif(
    os.name == "nt",
    reason="POSIX executable fixture; Windows bootstrap is checked on win-test",
)
def test_bootstrap_does_not_claim_a_reused_tool_on_path(tmp_path):
    script = tmp_path / "bootstrap.py"
    shutil.copy2(ROOT / "scripts/bootstrap.py", script)
    shutil.copy2(ROOT / "scripts/install_manifest.py", script.with_name("install_manifest.py"))
    archive = tmp_path / "tool.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for tool in ("just", "bun"):
            package.writestr(tool, f"#!{sys.executable}\nprint('26.13.0')\n")
        package.writestr("node-v26.13.0-linux-x64/include/node/", "")
        package.writestr(
            "node-v26.13.0-linux-x64/bin/node",
            f"#!{sys.executable}\nprint('26.13.0')\n",
        )
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "aarch64": "arm64"}.get(machine, machine)
    pins = {
        f"{platform.system().lower()}-{arch}": {
            tool: {"url": archive.as_uri(), "sha256": digest} for tool in ("just", "bun", "node")
        }
    }
    script.with_name("bootstrap-tools.json").write_text(json.dumps(pins))
    home = tmp_path / "home"
    home.mkdir()
    existing = tmp_path / "existing"
    existing.mkdir()
    just = existing / "just"
    just.write_text(f"#!{sys.executable}\nprint('1.58.0')\n")
    just.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(script)],
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": os.pathsep.join((str(existing), str(tmp_path / "empty"))),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Using just: 1.58.0" in result.stdout
    assert not (home / ".local/bin/just").exists()
    manifest = json.loads((home / ".local/share/garden/install-manifest.json").read_text())
    assert str(just) not in {entry["path"] for entry in manifest["artifacts"]}


@pytest.mark.parametrize(
    ("system", "marker"),
    [("linux", "Docker Engine"), ("darwin", "Docker Desktop"), ("windows", "WSL 2")],
)
def test_bootstrap_closing_message_has_platform_specific_docker_guidance(system, marker):
    assert marker in BOOTSTRAP.docker_guidance(system)
    assert "Docker" in BOOTSTRAP.docker_guidance(system)
    if system == "windows":
        assert "Docker CLI installed only inside WSL is not detected" in BOOTSTRAP.docker_guidance(
            system
        )


def test_bootstrap_ps1_requires_complete_portable_git_marker():
    script = (ROOT / "scripts/bootstrap.ps1").read_text()
    unconditional_check = (
        'USERPROFILE\\.local\\share\\garden-tools\\git\\bin\\bash.exe") | '
        "Where-Object { Test-Path $_ }"
    )
    assert unconditional_check not in script
    assert "$gitComplete = Join-Path $gitHome '.complete'" in script
    assert "$gitCompleteInManifest" in script
    assert "Set-Content -LiteralPath $gitComplete -Value 'complete'" in script
    assert "Record-Artifact $gitComplete 'file' $gitHome" in script
