from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "install_manifest", Path(__file__).resolve().parents[1] / "install_manifest.py"
)
assert SPEC and SPEC.loader
MANIFEST = importlib.util.module_from_spec(SPEC)
sys.modules["install_manifest"] = MANIFEST
SPEC.loader.exec_module(MANIFEST)


def test_reused_path_tool_is_not_recorded(tmp_path: Path) -> None:
    reused = tmp_path / "uv"
    reused.write_text("provided by the person")
    manifest = MANIFEST.empty_manifest()

    MANIFEST.record_artifact(manifest, reused, owner="bootstrap", kind="file", created=False)

    assert manifest["artifacts"] == []


def test_manifest_merges_created_artifacts_and_path_entries(tmp_path: Path) -> None:
    path = MANIFEST.manifest_path(tmp_path)
    manifest = MANIFEST.empty_manifest()
    launcher = tmp_path / ".local" / "bin" / "garden"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("garden launcher")
    MANIFEST.record_artifact(manifest, launcher, owner="agent-installer", kind="file")
    MANIFEST.record_path_entry(manifest, r"C:\Users\Person\.local\bin", owner="agent-installer")
    MANIFEST.save_manifest(path, manifest)

    loaded = MANIFEST.load_manifest(path)

    assert loaded["artifacts"][0]["path"] == str(launcher.resolve())
    assert loaded["path_entries"] == [
        {
            "created_value": False,
            "owner": "agent-installer",
            "scope": "windows-user",
            "value": r"C:\Users\Person\.local\bin",
        }
    ]
    assert json.loads(path.read_text())["version"] == 1


def test_preview_matches_artifacts_and_dry_run_removes_nothing(tmp_path: Path) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("created by Garden")
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_artifact(manifest, launcher, owner="agent-installer", kind="file")
    original = json.loads(json.dumps(manifest))

    preview = MANIFEST.artifact_plan(manifest)
    remaining, warnings = MANIFEST.apply_artifact_plan(manifest, preview, dry_run=True)

    assert [item["path"] for item in preview] == [str(launcher.resolve())]
    assert preview[0]["action"] == "remove"
    assert remaining == original
    assert warnings == []
    assert launcher.read_text() == "created by Garden"


def test_cleanup_removes_only_matching_managed_paths(tmp_path: Path) -> None:
    launcher = tmp_path / "garden"
    launcher.write_text("created by Garden")
    edited = tmp_path / "uv"
    edited.write_text("created by Garden")
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_artifact(manifest, launcher, owner="agent-installer", kind="file")
    MANIFEST.record_artifact(manifest, edited, owner="bootstrap", kind="file")
    edited.write_text("changed by the person")

    remaining, warnings = MANIFEST.apply_artifact_plan(
        manifest, MANIFEST.artifact_plan(manifest), trash_root=tmp_path / "trash"
    )

    assert not launcher.exists()
    assert (tmp_path / "trash/garden").read_text() == "created by Garden"
    assert edited.read_text() == "changed by the person"
    assert [entry["path"] for entry in remaining["artifacts"]] == [str(edited.resolve())]
    assert any("changed since Garden recorded it" in warning for warning in warnings)


def test_cleanup_removes_owned_symlink_without_following_its_target(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    reused = tmp_path / "existing-bun"
    reused.write_text("person's bun")
    launcher = bin_dir / "bunx"
    launcher.symlink_to(reused)
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_artifact(manifest, launcher, owner="bootstrap", kind="symlink", root=bin_dir)

    remaining, warnings = MANIFEST.apply_artifact_plan(
        manifest, MANIFEST.artifact_plan(manifest), trash_root=tmp_path / "trash"
    )

    assert not launcher.exists()
    assert reused.read_text() == "person's bun"
    assert remaining["artifacts"] == []
    assert warnings == []


def test_owned_path_removal_preserves_unrelated_entries() -> None:
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_path_entry(manifest, r"C:\Garden\bin", owner="bootstrap")
    MANIFEST.record_path_entry(manifest, r"C:\Garden\bin", owner="bootstrap")
    current = r"C:\User\bin;C:\Garden\bin;C:\Other\bin"

    updated, removed = MANIFEST.without_owned_path_entries(
        manifest, current, scope="windows-user", separator=";"
    )

    assert updated == r"C:\User\bin;C:\Other\bin"
    assert removed == [r"C:\Garden\bin"]


def test_docker_image_manifest_keeps_distinct_tags_for_the_same_image() -> None:
    manifest = MANIFEST.empty_manifest()

    MANIFEST.record_docker_image(manifest, "garden-demo-postgres:one", "sha256:same")
    MANIFEST.record_docker_image(manifest, "garden-demo-postgres:two", "sha256:same")
    MANIFEST.record_docker_image(manifest, "garden-demo-postgres:one", "sha256:same")

    assert manifest["docker_images"] == [
        {"reference": "garden-demo-postgres:one", "id": "sha256:same"},
        {"reference": "garden-demo-postgres:two", "id": "sha256:same"},
    ]


def test_owned_path_removal_is_case_insensitive_on_windows() -> None:
    manifest = MANIFEST.empty_manifest()
    MANIFEST.record_path_entry(manifest, r"C:\Garden\bin", owner="bootstrap")

    updated, removed = MANIFEST.without_owned_path_entries(
        manifest, r"C:\garden\BIN;C:\Other", scope="windows-user", separator=";"
    )

    assert updated == r"C:\Other"
    assert removed == [r"C:\garden\BIN"]
