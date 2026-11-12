"""Cleanup preview and safety checks for the demo runner."""

import argparse
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

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

    assert cleanup.data["logins"] == []
    assert "Revoke it in the portal's CLI logins page" in capsys.readouterr().err


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
