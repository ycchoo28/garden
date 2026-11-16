"""User-local, checksum-verified toolchain installation, invoked by the bootstrap wrappers."""

import hashlib
import json
import os
import platform
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

try:
    from install_manifest import (
        load_manifest,
        manifest_path,
        owns_artifact,
        record_artifact,
        record_path_entry,
        save_manifest,
    )
except ModuleNotFoundError:
    try:
        from scripts.install_manifest import (
            load_manifest,
            manifest_path,
            owns_artifact,
            record_artifact,
            record_path_entry,
            save_manifest,
        )
    except ModuleNotFoundError:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from install_manifest import (
            load_manifest,
            manifest_path,
            owns_artifact,
            record_artifact,
            record_path_entry,
            save_manifest,
        )


def _path_key(folder, system):
    value = os.path.normpath(str(folder))
    return value.lower() if system == "windows" else value


def remember_path(folder, tools, system, manifest, manifest_file):
    folder = Path(folder).resolve()
    current = os.environ.get("PATH", "").split(os.pathsep)
    present_in_process = _path_key(folder, system) in {
        _path_key(path, system) for path in current if path
    }
    if not present_in_process:
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
    paths_file = tools / "paths"
    old_paths = paths_file.read_text().splitlines() if paths_file.exists() else []
    if _path_key(folder, system) not in {_path_key(path, system) for path in old_paths}:
        existed = paths_file.exists()
        with paths_file.open("a") as paths:
            paths.write(str(folder) + "\n")
        record_artifact(
            manifest,
            paths_file,
            owner="bootstrap",
            kind="file",
            root=tools,
            created=not existed or owns_artifact(manifest, paths_file),
        )
        save_manifest(manifest_file, manifest)
    if (
        system == "windows"
        and not present_in_process
        and os.environ.get("GARDEN_BOOTSTRAP_NO_PERSIST") != "1"
    ):
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS
        ) as key:
            try:
                user_path = winreg.QueryValueEx(key, "Path")[0]
            except FileNotFoundError:
                user_path = ""
            if _path_key(folder, system) not in {
                _path_key(path, system) for path in user_path.split(";") if path
            }:
                had_user_path = bool(user_path.strip())
                winreg.SetValueEx(
                    key,
                    "Path",
                    0,
                    winreg.REG_EXPAND_SZ,
                    str(folder) + (";" + user_path if user_path else ""),
                )
                record_path_entry(
                    manifest,
                    str(folder),
                    owner="bootstrap",
                    created_value=not had_user_path,
                )
                save_manifest(manifest_file, manifest)


def docker_guidance(system):
    return {
        "linux": (
            "Linux: install Docker Engine and the Compose plugin, then start the daemon and ensure "
            "your user can access it. See https://docs.docker.com/engine/install/ and "
            "https://docs.docker.com/compose/install/linux/."
        ),
        "darwin": (
            "macOS: install and start Docker Desktop. "
            "See https://docs.docker.com/desktop/setup/install/mac-install/."
        ),
        "windows": (
            "Windows: native commands need Docker and Compose available on Windows. "
            "A Docker CLI installed only inside WSL is not detected; run the demo inside WSL or "
            "provide a session docker.cmd bridge. Otherwise use Docker Desktop with "
            "its WSL 2 backend and Linux containers. "
            "See https://docs.docker.com/desktop/setup/install/windows-install/."
        ),
    }.get(system, "Install Docker Engine with Docker Compose and start the Docker daemon.")


def main():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "arm64"}.get(machine, machine)
    target = f"{system}-{arch}"
    pins = json.loads(Path(__file__).with_name("bootstrap-tools.json").read_text())[target]
    bin_dir = Path.home() / ".local" / "bin"
    tools = Path.home() / ".local" / "share" / "garden-tools"
    manifest_file = manifest_path()
    manifest = load_manifest(manifest_file)
    tools_existed = tools.exists()
    tools.mkdir(parents=True, exist_ok=True)
    if not tools_existed or os.environ.get("GARDEN_BOOTSTRAP_TOOLS_CREATED") == "1":
        record_artifact(manifest, tools, owner="bootstrap", kind="tree", root=tools.parent)
        save_manifest(manifest_file, manifest)
    bin_existed = bin_dir.exists()
    bin_dir.mkdir(parents=True, exist_ok=True)
    if not bin_existed or os.environ.get("GARDEN_BOOTSTRAP_BIN_CREATED") == "1":
        record_artifact(manifest, bin_dir, owner="bootstrap", kind="directory", root=bin_dir.parent)
        save_manifest(manifest_file, manifest)
    uv_binary = bin_dir / ("uv.exe" if system == "windows" else "uv")
    if os.environ.get("GARDEN_BOOTSTRAP_UV_CREATED") == "1" and uv_binary.exists():
        record_artifact(manifest, uv_binary, owner="bootstrap", kind="file", root=bin_dir)
        save_manifest(manifest_file, manifest)
    paths_file = tools / "paths"
    if paths_file.exists():
        for folder in paths_file.read_text().splitlines():
            os.environ["PATH"] = folder + os.pathsep + os.environ["PATH"]
    for name, pin in pins.items():
        existing = shutil.which(name)
        if existing:
            version = subprocess.run(
                [existing, "--version"], capture_output=True, text=True, check=False
            )
            if version.returncode == 0 and (
                name != "node" or int(version.stdout.strip().lstrip("v").split(".")[0]) >= 24
            ):
                print(f"Using {name}: {version.stdout.strip()}", flush=True)
                continue
        # Keep downloaded working material in the system temp directory, never the repository.
        staging = Path(tempfile.mkdtemp(prefix=f"garden-bootstrap-{name}-"))
        archive = staging / pin["url"].rsplit("/", 1)[1]
        print(f"Installing {name} from {pin['url']}", flush=True)
        with (
            urllib.request.urlopen(pin["url"], timeout=60) as response,
            archive.open("wb") as handle,
        ):
            shutil.copyfileobj(response, handle)
        with archive.open("rb") as handle:
            if hashlib.file_digest(handle, "sha256").hexdigest() != pin["sha256"]:
                raise RuntimeError(f"{name} checksum mismatch; nothing extracted")
        destination = tools / name
        if destination.exists():
            if not owns_artifact(manifest, destination):
                raise RuntimeError(
                    f"Refusing to replace the unmanaged bootstrap path {destination}"
                )
            previous = tools / f"{name}.previous-{staging.name}"
            destination.rename(previous)
            for entry in manifest["artifacts"]:
                if _path_key(entry["path"], system) == _path_key(destination, system):
                    entry["path"] = str(previous)
                    entry["resolved_path"] = str(previous.resolve())
            record_artifact(manifest, previous, owner="bootstrap", kind="tree", root=tools)
            save_manifest(manifest_file, manifest)
        destination.mkdir(parents=True)
        record_artifact(manifest, destination, owner="bootstrap", kind="tree", root=tools)
        save_manifest(manifest_file, manifest)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as package:
                package.extractall(destination)
        else:
            with tarfile.open(archive) as package:
                package.extractall(destination, filter="data")
        executable = next(
            (
                candidate
                for candidate in destination.rglob(name + (".exe" if system == "windows" else ""))
                if candidate.is_file()
            ),
            None,
        )
        if executable is None:
            raise RuntimeError(f"{name} archive did not contain its executable")
        executable.chmod(0o755)
        if name == "node":
            # npm needs its files beside node; keep the full distribution intact.
            folder = executable.parent
        else:
            copied = bin_dir / executable.name
            if copied.exists() and not owns_artifact(manifest, copied):
                raise RuntimeError(f"Refusing to replace the unmanaged bootstrap file {copied}")
            shutil.copy2(executable, bin_dir / executable.name)
            folder = bin_dir
        record_artifact(manifest, destination, owner="bootstrap", kind="tree", root=tools)
        if name != "node":
            record_artifact(
                manifest,
                bin_dir / executable.name,
                owner="bootstrap",
                kind="file",
                root=bin_dir,
            )
        save_manifest(manifest_file, manifest)
        remember_path(folder, tools, system, manifest, manifest_file)
        subprocess.run([str(executable), "--version"], check=True)
    bunx = shutil.which("bunx")
    if not bunx:
        bun = shutil.which("bun")
        launcher = bin_dir / ("bunx.exe" if system == "windows" else "bunx")
        if launcher.exists() and not owns_artifact(manifest, launcher):
            raise RuntimeError(f"Refusing to replace the unmanaged bootstrap file {launcher}")
        if not launcher.exists():
            if system == "windows":
                shutil.copy2(bun, launcher)
                record_artifact(manifest, launcher, owner="bootstrap", kind="file", root=bin_dir)
            else:
                launcher.symlink_to(bun)
                record_artifact(manifest, launcher, owner="bootstrap", kind="symlink", root=bin_dir)
        save_manifest(manifest_file, manifest)
        remember_path(bin_dir, tools, system, manifest, manifest_file)
    save_manifest(manifest_file, manifest)
    print(f"Toolchains ready. Before running a demo, {docker_guidance(system)}")


if __name__ == "__main__":
    main()
