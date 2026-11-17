"""Record the user-local files and settings created by Garden installers and demos."""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

VERSION = 1


def manifest_path(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local" / "share" / "garden" / "install-manifest.json"


def empty_manifest() -> dict:
    return {
        "version": VERSION,
        "artifacts": [],
        "path_entries": [],
        "demo_projects": [],
        "docker_images": [],
        "logins": [],
        "checkouts": {},
    }


def load_manifest(path: Path | None = None) -> dict:
    target = path or manifest_path()
    if not target.exists():
        return empty_manifest()
    data = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("version") != VERSION:
        raise ValueError(f"Unsupported or invalid Garden install manifest: {target}")
    # Older manifests have no checkout baselines; installers that predate the key keep it intact.
    if not isinstance(data.setdefault("checkouts", {}), dict):
        raise ValueError(f"Invalid checkouts in Garden install manifest: {target}")
    for name in (
        "artifacts",
        "path_entries",
        "demo_projects",
        "docker_images",
        "logins",
    ):
        if not isinstance(data.get(name), list):
            raise ValueError(f"Invalid {name} in Garden install manifest: {target}")
    return data


def save_manifest(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix="garden-manifest-", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _windows_path_key(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(path.strip().strip('"')))


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def record_artifact(
    data: dict,
    path: str | Path,
    *,
    owner: str,
    kind: str,
    root: str | Path | None = None,
    created: bool = True,
) -> bool:
    """Record a newly created path, or refresh one already owned by this manifest."""
    if not created:
        return False
    if kind not in {"file", "directory", "tree", "symlink"}:
        raise ValueError(f"Unsupported Garden artifact kind: {kind}")
    target = Path(path).expanduser().absolute()
    if not os.path.lexists(target):
        raise FileNotFoundError(target)
    if kind == "file" and not target.is_file():
        raise ValueError(f"Expected a Garden file: {target}")
    if kind == "symlink" and not target.is_symlink():
        raise ValueError(f"Expected a Garden symlink: {target}")
    if kind in {"directory", "tree"} and (not target.is_dir() or target.is_symlink()):
        raise ValueError(f"Expected a Garden directory: {target}")
    resolved = target.resolve(strict=False)
    item = {
        "path": str(target),
        "resolved_path": str(resolved),
        "root": str(Path(root).expanduser().absolute().resolve(strict=False))
        if root
        else str(resolved),
        "kind": kind,
        "owner": owner,
    }
    if kind == "file":
        item["sha256"] = _file_hash(target)
    elif kind == "symlink":
        item["target"] = os.readlink(target)
    key = _path_key(target)
    for index, existing in enumerate(data["artifacts"]):
        if _path_key(existing["path"]) == key:
            if existing.get("kind") == "tree":
                item["kind"] = "tree"
            item["root"] = existing.get("root", item["root"])
            data["artifacts"][index] = item
            return True
    data["artifacts"].append(item)
    return True


def owns_artifact(data: dict, path: str | Path) -> bool:
    key = _path_key(path)
    return any(_path_key(entry["path"]) == key for entry in data["artifacts"])


def record_path_entry(
    data: dict,
    value: str,
    *,
    owner: str,
    scope: str = "windows-user",
    created_value: bool = False,
) -> bool:
    if not value:
        return False
    key = _windows_path_key(value) if scope == "windows-user" else os.path.normpath(value)
    for entry in data["path_entries"]:
        existing = entry["value"]
        existing_key = (
            _windows_path_key(existing) if scope == "windows-user" else os.path.normpath(existing)
        )
        if entry.get("scope") == scope and existing_key == key:
            return False
    data["path_entries"].append(
        {
            "value": value,
            "scope": scope,
            "owner": owner,
            "created_value": created_value,
        }
    )
    return True


def without_owned_path_entries(
    data: dict, current: str, *, scope: str, separator: str
) -> tuple[str, list[str]]:
    if scope == "windows-user":
        owned = {
            _windows_path_key(entry["value"])
            for entry in data["path_entries"]
            if entry.get("scope") == scope
        }
        key = _windows_path_key
    else:
        owned = {
            os.path.normpath(entry["value"])
            for entry in data["path_entries"]
            if entry.get("scope") == scope
        }
        key = os.path.normpath
    parts = current.split(separator) if current else []
    removed = [part for part in parts if part and key(part) in owned]
    updated = separator.join(part for part in parts if not part or key(part) not in owned)
    return updated, removed


def record_demo_project(
    data: dict,
    root: str | Path,
    name: str,
    *,
    image: str | None = None,
    active: bool = True,
    **details: str | int | bool | None,
) -> None:
    resolved_root = str(Path(root).resolve(strict=False))
    for project in data["demo_projects"]:
        if project.get("root") == resolved_root:
            project.update({"name": name, "active": active, **details})
            if image:
                project["image"] = image
            return
    project = {"root": resolved_root, "name": name, "active": active, **details}
    if image:
        project["image"] = image
    data["demo_projects"].append(project)


def record_docker_image(data: dict, reference: str, image_id: str) -> None:
    if not any(
        image.get("reference") == reference and image.get("id") == image_id
        for image in data["docker_images"]
    ):
        data["docker_images"].append({"reference": reference, "id": image_id})


def record_login(data: dict, portal: str, launcher: str, garden_home: str | None = None) -> None:
    for login in data["logins"]:
        if login.get("portal") == portal:
            login["launcher"] = launcher
            if garden_home:
                login["garden_home"] = garden_home
            return
    login = {"portal": portal, "launcher": launcher}
    if garden_home:
        login["garden_home"] = garden_home
    data["logins"].append(login)


def _unique_trash_path(root: Path, name: str) -> Path:
    target = root / name
    if not os.path.lexists(target):
        return target
    suffix = 1
    while os.path.lexists(root / f"{name}.{suffix}"):
        suffix += 1
    return root / f"{name}.{suffix}"


def _trash_path(path: Path, trash_root: Path | None = None) -> None:
    if trash_root is not None:
        trash_root.mkdir(parents=True, exist_ok=True)
        path.rename(_unique_trash_path(trash_root, path.name))
        return
    command = shutil.which("trash")
    if command:
        try:
            subprocess.run([command, str(path)], check=True)
            return
        except (OSError, subprocess.SubprocessError):
            if not os.path.lexists(path):
                return
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class FileOperation(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        source = ctypes.create_unicode_buffer(str(path) + "\0")
        operation = FileOperation(
            None,
            3,
            ctypes.cast(source, wintypes.LPCWSTR),
            None,
            0x40 | 0x10 | 0x4,
            False,
            None,
            None,
        )
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
        if result or operation.fAnyOperationsAborted:
            raise OSError(result, f"Windows could not move {path} to the Recycle Bin")
        return
    if sys.platform == "darwin":
        root = Path.home() / ".Trash"
        root.mkdir(parents=True, exist_ok=True)
        path.rename(_unique_trash_path(root, path.name))
        return

    data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    trash = data_home / "Trash"
    files = trash / "files"
    info = trash / "info"
    files.mkdir(parents=True, exist_ok=True)
    info.mkdir(parents=True, exist_ok=True)
    destination = _unique_trash_path(files, path.name)
    path.rename(destination)
    metadata = info / f"{destination.name}.trashinfo"
    escaped_path = quote(str(path.absolute()), safe="/")
    deleted = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S")
    metadata.write_text(
        f"[Trash Info]\nPath={escaped_path}\nDeletionDate={deleted}\n",
        encoding="utf-8",
    )


def trash_path(path: str | Path) -> None:
    _trash_path(Path(path))


def artifact_plan(data: dict, *, preserve: tuple[str | Path, ...] = ()) -> list[dict]:
    preserved = {_path_key(path) for path in preserve}
    entries = sorted(
        data["artifacts"],
        key=lambda item: (
            item.get("kind") in {"directory", "tree"},
            -len(item["path"]),
        ),
    )
    plan = []
    for entry in entries:
        target = Path(entry["path"])
        if _path_key(target) in preserved:
            action, reason = "keep", "separate confirmation required"
        elif not os.path.lexists(target):
            action, reason = "missing", "already absent"
        elif str(target.resolve(strict=False)) != entry.get("resolved_path"):
            action, reason = "skip", "path now resolves somewhere else"
        else:
            root = Path(entry.get("root", entry["resolved_path"]))
            containment_path = (
                target.parent.resolve(strict=False) / target.name
                if entry.get("kind") == "symlink"
                else target.resolve(strict=False)
            )
            try:
                containment_path.relative_to(root.resolve(strict=False))
            except ValueError:
                action, reason = "skip", "path is outside its recorded Garden root"
            else:
                action, reason = "remove", "recorded Garden artifact"
                kind = entry.get("kind")
                if kind == "file" and (not target.is_file() or target.is_symlink()):
                    action, reason = "skip", "file type changed"
                elif kind == "file" and _file_hash(target) != entry.get("sha256"):
                    action, reason = "skip", "file changed since Garden recorded it"
                elif kind == "symlink" and (
                    not target.is_symlink() or os.readlink(target) != entry.get("target")
                ):
                    action, reason = "skip", "symlink changed since Garden recorded it"
                elif kind in {"directory", "tree"} and (not target.is_dir() or target.is_symlink()):
                    action, reason = "skip", "directory type changed"
                elif kind not in {"file", "directory", "tree", "symlink"}:
                    action, reason = "skip", "unknown artifact type"
        plan.append({**entry, "action": action, "reason": reason})
    return plan


def apply_artifact_plan(
    data: dict,
    plan: list[dict],
    *,
    dry_run: bool = False,
    trash_root: Path | None = None,
) -> tuple[dict, list[str]]:
    if dry_run:
        return {**data, "artifacts": list(data["artifacts"])}, []
    remaining = {**data, "artifacts": []}
    warnings = []
    for item in plan:
        action = item["action"]
        entry = {key: value for key, value in item.items() if key not in {"action", "reason"}}
        if action == "missing":
            continue
        if action != "remove":
            remaining["artifacts"].append(entry)
            warnings.append(f"Skipping {item['path']}: {item['reason']}.")
            continue
        checked = artifact_plan({**data, "artifacts": [entry]})[0]
        if checked["action"] != "remove":
            remaining["artifacts"].append(entry)
            warnings.append(f"Skipping {item['path']}: {checked['reason']}.")
            continue
        target = Path(item["path"])
        try:
            if item["kind"] == "directory":
                target.rmdir()
            else:
                _trash_path(target, trash_root)
        except OSError as error:
            remaining["artifacts"].append(entry)
            warnings.append(f"Could not remove {item['path']}: {error}.")
    return remaining, warnings
