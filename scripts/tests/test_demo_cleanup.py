"""Cleanup preview and safety checks for the demo runner."""

import argparse
import importlib.util
import json
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

MANIFEST_SPEC = importlib.util.spec_from_file_location(
    "install_manifest", Path(__file__).resolve().parents[1] / "install_manifest.py"
)
assert MANIFEST_SPEC and MANIFEST_SPEC.loader
MANIFEST = importlib.util.module_from_spec(MANIFEST_SPEC)
sys.modules["install_manifest"] = MANIFEST
MANIFEST_SPEC.loader.exec_module(MANIFEST)

SPEC = importlib.util.spec_from_file_location(
    "demo_cleanup", Path(__file__).resolve().parents[1] / "demo.py"
)
assert SPEC and SPEC.loader
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)

CONFIG_SPEC = importlib.util.spec_from_file_location(
    "garden_backend_config", Path(__file__).resolve().parents[2] / "backend/app/config.py"
)
assert CONFIG_SPEC and CONFIG_SPEC.loader
CONFIG = importlib.util.module_from_spec(CONFIG_SPEC)
CONFIG_SPEC.loader.exec_module(CONFIG)


def test_clean_preview_keeps_login_and_installs() -> None:
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_demo_project(manifest, DEMO.ROOT, "garden-demo-test", active=True)
    MANIFEST.record_login(manifest, "http://127.0.0.1:55000", "/home/person/.local/bin/garden")

    preview = DEMO.cleanup_preview(manifest, "clean")

    assert any("Compose down --volumes" in line for line in preview)
    assert any("Keep Garden tools, installs, CLI login" in line for line in preview)
    assert any("will no longer work" in line for line in preview)
    assert not any("garden logout" in line for line in preview)


def test_demo_port_preflight_rejects_an_active_listener() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        with pytest.raises(RuntimeError, match=f"Demo loopback port {port} is unavailable"):
            DEMO.check_demo_port(port)


def test_empty_web_only_model_environment_overrides_populated_dotenv(
    tmp_path: Path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "GARDEN_DATABASE_URL=postgresql://localhost/garden\n"
        "GARDEN_AGENT_MODEL_UPSTREAM_URL=https://model.example/v1\n"
        "GARDEN_AGENT_MODEL_API_KEY=saved-token\n"
        "GARDEN_AGENT_MODEL_MODELS=saved-model\n"
        "GARDEN_AGENT_MODEL_DEFAULT=saved-model\n"
    )
    for key in DEMO.MODEL_KEYS:
        monkeypatch.setenv(key, "")

    settings = CONFIG.Settings(_env_file=env_file)

    assert settings.agent_model_upstream_url == ""
    assert settings.agent_model_api_key.get_secret_value() == ""
    assert settings.allowed_model_names == []
    assert settings.agent_model_default == ""


def test_uninstall_preview_lists_only_recorded_files_and_path_entries(
    tmp_path: Path,
) -> None:
    manifest = MANIFEST.empty_manifest()
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    MANIFEST.record_artifact(manifest, launcher, owner="agent-installer", kind="file")
    manifest["path_entries"].append(
        {"value": r"C:\Garden\bin", "scope": "windows-user", "owner": "agent-installer"}
    )
    MANIFEST.record_login(manifest, "http://127.0.0.1:55000", str(launcher))
    env_file = tmp_path / ".env"
    env_file.write_text("secret=keep\n")

    preview = DEMO.cleanup_preview(manifest, "uninstall", remove_env=False, env_file=env_file)

    assert any("garden logout" in line for line in preview)
    assert any(str(launcher) in line and "Move to Trash" in line for line in preview)
    assert any(r"C:\Garden\bin" in line for line in preview)
    assert any(f"Keep {env_file}" in line for line in preview)
    assert env_file.read_text() == "secret=keep\n"


def test_dry_run_does_not_logout_or_change_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    data = MANIFEST.empty_manifest()
    MANIFEST.record_artifact(data, launcher, owner="agent-installer", kind="file")
    MANIFEST.record_login(data, "http://127.0.0.1:55000", str(launcher))
    path = MANIFEST.manifest_path(tmp_path)
    MANIFEST.save_manifest(path, data)
    original = json.loads(path.read_text())
    args = argparse.Namespace(
        dry_run=True,
        yes=False,
        remove_env=False,
        env_file=str(tmp_path / ".env"),
    )
    cleanup = DEMO.DemoCleanup(args, "uninstall")
    cleanup.logout = lambda: (_ for _ in ()).throw(AssertionError("logout ran during dry-run"))

    assert cleanup.run() == 0
    assert launcher.exists()
    assert json.loads(path.read_text()) == original


def test_unreachable_portal_warns_where_to_revoke_login(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    cleanup = object.__new__(DEMO.DemoCleanup)
    cleanup.data = {
        "logins": [
            {
                "portal": "http://127.0.0.1:55000",
                "launcher": str(launcher),
                "garden_home": str(tmp_path / ".garden"),
            }
        ]
    }
    cleanup.failed = False
    monkeypatch.setattr(
        DEMO.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[str(launcher), "logout"],
            returncode=0,
            stdout='{"signed_out": true}\n',
            stderr="warning: the portal did not confirm the sign-out (unreachable).\n",
        ),
    )

    cleanup.logout()

    assert cleanup.failed is True
    assert len(cleanup.data["logins"]) == 1
    err = capsys.readouterr().err
    assert (
        "could not be revoked and remains valid until it expires or is revoked in the portal" in err
    )
    assert "Warning: warning:" not in err


@pytest.mark.parametrize("flag_attr", ["force", "drop_login"])
def test_force_removes_record_when_portal_unreachable(
    flag_attr: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    cleanup = object.__new__(DEMO.DemoCleanup)
    cleanup.args = argparse.Namespace(**{flag_attr: True})
    cleanup.data = {
        "logins": [
            {
                "portal": "http://127.0.0.1:55000",
                "launcher": str(launcher),
                "garden_home": str(tmp_path / ".garden"),
            }
        ]
    }
    cleanup.failed = False
    monkeypatch.setattr(
        DEMO.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[str(launcher), "logout", "--force"],
            returncode=0,
            stdout='{"signed_out": true, "unconfirmed": true}\n',
            stderr=(
                "warning: the portal did not confirm the sign-out (unreachable). "
                "The local login was dropped (--force).\n"
            ),
        ),
    )

    cleanup.logout()

    assert cleanup.failed is False
    assert len(cleanup.data["logins"]) == 0
    err = capsys.readouterr().err
    assert "Warning: the portal did not confirm sign-out" in err
    assert "Warning: warning:" not in err
    assert "The local login record was removed (--force)." in err
    assert (
        "The server-side login remains valid on the portal until it expires or is revoked." in err
    )


def test_force_keeps_record_on_nonzero_cli_exit(tmp_path: Path, monkeypatch, capsys) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    cleanup = object.__new__(DEMO.DemoCleanup)
    cleanup.args = argparse.Namespace(force=True)
    cleanup.data = {
        "logins": [
            {
                "portal": "http://127.0.0.1:55000",
                "launcher": str(launcher),
                "garden_home": str(tmp_path / ".garden"),
            }
        ]
    }
    cleanup.failed = False
    monkeypatch.setattr(
        DEMO.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[str(launcher), "logout", "--force"],
            returncode=2,
            stdout="",
            stderr=(
                "usage: garden logout [-h] [--json]\n"
                "garden logout: error: unrecognized arguments: --force\n"
            ),
        ),
    )

    cleanup.logout()

    assert cleanup.failed is True
    assert len(cleanup.data["logins"]) == 1
    err = capsys.readouterr().err
    assert (
        "could not be revoked and remains valid until it expires or is revoked in the portal" in err
    )


def test_force_keeps_record_on_subprocess_timeout(tmp_path: Path, monkeypatch, capsys) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    cleanup = object.__new__(DEMO.DemoCleanup)
    cleanup.args = argparse.Namespace(force=True)
    cleanup.data = {
        "logins": [
            {
                "portal": "http://127.0.0.1:55000",
                "launcher": str(launcher),
                "garden_home": str(tmp_path / ".garden"),
            }
        ]
    }
    cleanup.failed = False

    def timeout_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=[str(launcher), "logout", "--force"], timeout=30)

    monkeypatch.setattr(DEMO.subprocess, "run", timeout_run)

    cleanup.logout()

    assert cleanup.failed is True
    assert len(cleanup.data["logins"]) == 1
    err = capsys.readouterr().err
    assert "garden logout failed" in err
    assert (
        "could not be revoked and remains valid until it expires or is revoked in the portal" in err
    )


def test_logout_child_client_timeout_is_below_subprocess_limit(tmp_path: Path, monkeypatch) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    cleanup = object.__new__(DEMO.DemoCleanup)
    cleanup.args = argparse.Namespace(force=True)
    cleanup.data = {
        "logins": [
            {
                "portal": "http://127.0.0.1:55000",
                "launcher": str(launcher),
                "garden_home": str(tmp_path / ".garden"),
            }
        ]
    }
    cleanup.failed = False

    captured: dict[str, Any] = {}

    def capture_run(cmd, env=None, timeout=None, **_kwargs):
        captured["cmd"] = cmd
        captured["env"] = env or {}
        captured["timeout"] = timeout
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='{"signed_out": true}',
            stderr=(
                "warning: the portal did not confirm the sign-out (The portal cannot be reached). "
                "The local login was dropped (--force).\n"
            ),
        )

    monkeypatch.setattr(DEMO.subprocess, "run", capture_run)

    cleanup.logout()

    assert cleanup.failed is False
    assert len(cleanup.data["logins"]) == 0
    assert "GARDEN_CLIENT_TIMEOUT" in captured["env"]
    client_timeout = float(captured["env"]["GARDEN_CLIENT_TIMEOUT"])
    subprocess_timeout = float(captured["timeout"])
    assert client_timeout < subprocess_timeout
    assert client_timeout == 10.0
    assert subprocess_timeout == 30.0


@contextmanager
def hanging_server() -> Generator[str]:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(5)
    port = server.getsockname()[1]
    running = True

    def handler() -> None:
        while running:
            try:
                server.settimeout(0.2)
                conn, _ = server.accept()
                while running:
                    time.sleep(0.1)
                conn.close()
            except (TimeoutError, OSError):
                pass

    thread = threading.Thread(target=handler, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        running = False
        server.close()
        thread.join(timeout=1.0)


def test_logout_force_drop_completes_when_portal_never_responds(
    tmp_path: Path, monkeypatch
) -> None:
    agent_dir = Path(__file__).resolve().parents[2] / "agent"
    launcher = tmp_path / ("garden.cmd" if sys.platform == "win32" else "garden")
    if sys.platform == "win32":
        launcher.write_text(f'@echo off\nuv run --project "{agent_dir}" python -m garden %*\n')
    else:
        launcher.write_text(
            f'#!/bin/sh\nexec uv run --project "{agent_dir}" python -m garden "$@"\n'
        )
        launcher.chmod(0o755)

    with hanging_server() as portal_url:
        secrets_file = tmp_path / "secrets.json"
        secrets_file.write_text(
            json.dumps({portal_url: json.dumps({"token": "secret-token", "device": "laptop"})})
        )
        monkeypatch.setenv("GARDEN_SECRET_STORE", f"file:{secrets_file}")
        monkeypatch.setenv("GARDEN_CLIENT_TIMEOUT", "0.3")

        cleanup = object.__new__(DEMO.DemoCleanup)
        cleanup.args = argparse.Namespace(force=True)
        cleanup.data = {
            "logins": [
                {
                    "portal": portal_url,
                    "launcher": str(launcher),
                    "garden_home": str(tmp_path / ".garden"),
                }
            ]
        }
        cleanup.failed = False

        cleanup.logout()

        assert cleanup.failed is False
        assert len(cleanup.data["logins"]) == 0
        assert "secret-token" not in secrets_file.read_text()


def test_force_keeps_record_on_os_error(tmp_path: Path, monkeypatch, capsys) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("Garden launcher")
    cleanup = object.__new__(DEMO.DemoCleanup)
    cleanup.args = argparse.Namespace(force=True)
    cleanup.data = {
        "logins": [
            {
                "portal": "http://127.0.0.1:55000",
                "launcher": str(launcher),
                "garden_home": str(tmp_path / ".garden"),
            }
        ]
    }
    cleanup.failed = False

    def error_run(*_args, **_kwargs):
        raise OSError("Executable file not found")

    monkeypatch.setattr(DEMO.subprocess, "run", error_run)

    cleanup.logout()

    assert cleanup.failed is True
    assert len(cleanup.data["logins"]) == 1
    err = capsys.readouterr().err
    assert "garden logout failed" in err
    assert (
        "could not be revoked and remains valid until it expires or is revoked in the portal" in err
    )


def test_compose_teardown_runs_when_the_runner_is_already_gone(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    data = MANIFEST.empty_manifest()
    MANIFEST.record_demo_project(
        data,
        DEMO.ROOT,
        "garden-demo-test",
        active=True,
        control_url="http://127.0.0.1:1/stop/gone",
    )
    MANIFEST.save_manifest(MANIFEST.manifest_path(tmp_path), data)
    args = argparse.Namespace(
        dry_run=False,
        yes=True,
        remove_env=False,
        env_file=str(tmp_path / ".env"),
    )
    cleanup = DEMO.DemoCleanup(args, "clean")
    torn_down = []
    monkeypatch.setattr(cleanup, "stop_runner", lambda _project: False)
    monkeypatch.setattr(
        cleanup, "compose_down", lambda project: torn_down.append(project["name"]) or True
    )

    assert cleanup.run() == 1
    assert torn_down == ["garden-demo-test"]


def test_empty_cleanup_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    args = argparse.Namespace(
        dry_run=False,
        yes=False,
        remove_env=False,
        env_file=str(tmp_path / ".env"),
    )

    assert DEMO.DemoCleanup(args, "clean").run() == 0
    assert DEMO.DemoCleanup(args, "uninstall").run() == 0


def test_shared_directory_is_removed_only_when_empty(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = bin_dir / "garden"
    launcher.write_text("Garden launcher")
    reused = bin_dir / "uv"
    reused.write_text("person's tool")
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_artifact(manifest, bin_dir, owner="bootstrap", kind="directory", root=tmp_path)
    MANIFEST.record_artifact(manifest, launcher, owner="agent-installer", kind="file", root=bin_dir)

    remaining, warnings = DEMO.apply_artifact_plan(
        manifest,
        DEMO.artifact_plan(manifest),
        trash_root=tmp_path / "trash",
    )

    assert not launcher.exists()
    assert reused.read_text() == "person's tool"
    assert bin_dir.exists()
    assert any("Directory not empty" in warning or "not empty" in warning for warning in warnings)
    assert remaining["artifacts"] == [manifest["artifacts"][0]]


def test_restore_user_path_prunes_manifest_when_path_already_cleared(monkeypatch) -> None:
    import contextlib
    import sys
    import types

    monkeypatch.setattr(DEMO.os, "name", "nt")
    fake_winreg = types.ModuleType("winreg")
    fake_winreg.HKEY_CURRENT_USER = 1
    fake_winreg.KEY_ALL_ACCESS = 1
    fake_winreg.REG_EXPAND_SZ = 1
    fake_winreg.OpenKey = lambda *args, **kwargs: contextlib.nullcontext(object())
    fake_winreg.QueryValueEx = lambda _key, _name: (r"C:\Other\bin", 1)
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    cleanup = object.__new__(DEMO.DemoCleanup)
    cleanup.data = {
        "path_entries": [
            {
                "scope": "windows-user",
                "value": r"C:\Users\test\.local\bin",
                "owner": "agent-installer",
            }
        ]
    }
    cleanup.restore_user_path()
    assert cleanup.data["path_entries"] == []


PORTAL = "http://127.0.0.1:55000"
UNCONFIRMED_LOGOUT = (
    "warning: the portal did not confirm the sign-out (The portal cannot be reached). "
    "The login was kept locally so sign-out can be retried.\n"
)
FORCED_LOGOUT = (
    "warning: the portal did not confirm the sign-out (The portal cannot be reached). "
    "The local login was dropped (--force).\n"
)


def _test_trash(trash: Path):
    """Move "trashed" files into a test folder so nothing reaches the real Trash."""
    trash.mkdir(parents=True, exist_ok=True)

    def move(path, trash_root=None):
        source = Path(path)
        source.rename(trash / f"{len(list(trash.iterdir()))}-{source.name}")

    return move


def _fake_logout(monkeypatch, returncode: int, stderr: str, seen: list) -> None:
    def run(cmd, **_kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(
            args=cmd, returncode=returncode, stdout='{"signed_out": true}\n', stderr=stderr
        )

    monkeypatch.setattr(DEMO.subprocess, "run", run)


def _cleanup_args(env_root: Path, *, force: bool = False) -> argparse.Namespace:
    return argparse.Namespace(
        dry_run=False,
        yes=True,
        remove_env=False,
        env_file=str(env_root / ".env"),
        force=force,
    )


@pytest.fixture
def installed_demo(tmp_path: Path, monkeypatch) -> dict[str, Any]:
    """A bootstrap plus agent install with one CLI login, as `just demo` records it."""
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    bin_dir = home / ".local" / "bin"
    garden_home = home / ".garden"
    dsh_home = home / ".dsh"
    tools = home / ".local" / "share" / "garden-tools" / "just-1.58.0"
    paths: dict[str, Any] = {
        "home": home,
        "trash": tmp_path / "trash",
        "bin": bin_dir,
        "launcher": bin_dir / "garden",
        "dsh_launcher": bin_dir / "dsh",
        "just": bin_dir / "just",
        "uv": bin_dir / "uv",
        "garden_home": garden_home,
        "pyz": garden_home / "garden.pyz",
        "config": garden_home / "config.json",
        "dsh_home": dsh_home,
        "tools": tools,
    }
    for name in ("launcher", "dsh_launcher", "just", "uv", "pyz", "config"):
        paths[name].parent.mkdir(parents=True, exist_ok=True)
        paths[name].write_text(name)
    (dsh_home / "bundle.txt").parent.mkdir(parents=True, exist_ok=True)
    (dsh_home / "bundle.txt").write_text("dsh")
    (tools / "just").parent.mkdir(parents=True, exist_ok=True)
    (tools / "just").write_text("just tool")

    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_artifact(manifest, bin_dir, owner="bootstrap", kind="directory", root=home)
    MANIFEST.record_artifact(manifest, paths["just"], owner="bootstrap", kind="file", root=bin_dir)
    MANIFEST.record_artifact(manifest, paths["uv"], owner="bootstrap", kind="file", root=bin_dir)
    MANIFEST.record_artifact(manifest, tools, owner="bootstrap", kind="tree", root=tools.parent)
    MANIFEST.record_artifact(
        manifest, paths["launcher"], owner="agent-installer", kind="file", root=bin_dir
    )
    MANIFEST.record_artifact(
        manifest, paths["dsh_launcher"], owner="agent-installer", kind="file", root=bin_dir
    )
    MANIFEST.record_artifact(manifest, garden_home, owner="agent-installer", kind="tree", root=home)
    MANIFEST.record_artifact(manifest, dsh_home, owner="agent-installer", kind="tree", root=home)
    MANIFEST.record_login(manifest, PORTAL, str(paths["launcher"]), str(garden_home))
    manifest_file = MANIFEST.manifest_path(home)
    MANIFEST.save_manifest(manifest_file, manifest)
    monkeypatch.setattr(MANIFEST, "_trash_path", _test_trash(paths["trash"]))
    paths["manifest_file"] = manifest_file
    return paths


def test_unconfirmed_uninstall_keeps_launcher_and_recovery_files(
    installed_demo: dict[str, Any], tmp_path: Path, monkeypatch, capsys
) -> None:
    seen: list[list[str]] = []
    _fake_logout(monkeypatch, 1, UNCONFIRMED_LOGOUT, seen)

    assert DEMO.DemoCleanup(_cleanup_args(tmp_path), "uninstall").run() == 1

    for kept in ("launcher", "pyz", "config", "garden_home", "bin", "just", "uv"):
        assert installed_demo[kept].exists(), kept
    for removed in ("dsh_launcher", "dsh_home", "tools"):
        assert not installed_demo[removed].exists(), removed
    saved = MANIFEST.load_manifest(installed_demo["manifest_file"])
    assert [login["portal"] for login in saved["logins"]] == [PORTAL]
    kept_records = {entry["path"] for entry in saved["artifacts"]}
    assert str(installed_demo["launcher"]) in kept_records
    assert str(installed_demo["garden_home"]) in kept_records
    assert str(installed_demo["dsh_launcher"]) not in kept_records
    out = capsys.readouterr().out
    assert f"Kept garden at {installed_demo['launcher']}" in out
    assert "just demo-uninstall --drop-login" in out


def test_drop_login_finishes_after_unconfirmed_uninstall(
    installed_demo: dict[str, Any], tmp_path: Path, monkeypatch
) -> None:
    _fake_logout(monkeypatch, 1, UNCONFIRMED_LOGOUT, [])
    assert DEMO.DemoCleanup(_cleanup_args(tmp_path), "uninstall").run() == 1

    seen: list[list[str]] = []
    _fake_logout(monkeypatch, 0, FORCED_LOGOUT, seen)
    assert DEMO.DemoCleanup(_cleanup_args(tmp_path, force=True), "uninstall").run() == 0

    # The launcher the recovery run needs was still on disk when it ran.
    assert seen == [[str(installed_demo["launcher"]), "logout", "--json", "--force"]]
    for name in ("launcher", "pyz", "config", "garden_home", "bin", "just", "uv"):
        assert not installed_demo[name].exists(), name
    assert not installed_demo["manifest_file"].exists()


def test_drop_login_drops_record_when_launcher_is_missing(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    trash = tmp_path / "trash"
    monkeypatch.setattr(MANIFEST, "_trash_path", _test_trash(trash))
    leftover = home / ".garden" / "garden.pyz"
    leftover.parent.mkdir(parents=True)
    leftover.write_text("pyz")
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_artifact(
        manifest, leftover, owner="agent-installer", kind="file", root=leftover.parent
    )
    missing = home / ".local" / "bin" / "garden"
    MANIFEST.record_login(manifest, PORTAL, str(missing), str(leftover.parent))
    manifest_file = MANIFEST.manifest_path(home)
    MANIFEST.save_manifest(manifest_file, manifest)
    seen: list[list[str]] = []
    _fake_logout(monkeypatch, 0, FORCED_LOGOUT, seen)

    assert DEMO.DemoCleanup(_cleanup_args(tmp_path, force=True), "uninstall").run() == 0

    assert seen == []
    assert not leftover.exists()
    assert not manifest_file.exists()
    err = capsys.readouterr().err
    assert f"garden is missing at {missing}" in err
    assert "The login record was dropped (--drop-login)" in err
    assert "could not be removed" in err


def test_path_restore_keeps_entries_for_kept_recovery_folders() -> None:
    entries = [
        {"value": r"C:\Users\Person\.local\bin", "scope": "windows-user", "owner": "bootstrap"},
        {"value": r"C:\Users\Person\Tools", "scope": "windows-user", "owner": "agent-installer"},
    ]
    keep = {DEMO._path_text(r"C:\Users\Person\.local\bin")}

    assert DEMO.path_entries_to_restore(entries, keep) == [entries[1]]
