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


def remember_path(folder, tools, system):
    os.environ["PATH"] = str(folder) + os.pathsep + os.environ["PATH"]
    with (tools / "paths").open("a") as paths:
        paths.write(str(folder) + "\n")
    if system == "windows" and os.environ.get("GARDEN_BOOTSTRAP_NO_PERSIST") != "1":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_ALL_ACCESS
        ) as key:
            try:
                current = winreg.QueryValueEx(key, "Path")[0]
            except FileNotFoundError:
                current = ""
            if str(folder).lower() not in current.lower().split(";"):
                winreg.SetValueEx(
                    key,
                    "Path",
                    0,
                    winreg.REG_EXPAND_SZ,
                    str(folder) + ";" + current,
                )


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
    bin_dir.mkdir(parents=True, exist_ok=True)
    tools.mkdir(parents=True, exist_ok=True)
    paths_file = tools / "paths"
    if paths_file.exists():
        for folder in paths_file.read_text().splitlines():
            os.environ["PATH"] = folder + os.pathsep + os.environ["PATH"]
    paths_file.write_text("")
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
                remember_path(Path(existing).parent, tools, system)
                continue
        # Keep downloaded working material in the system temp directory, never the repository.
        staging = Path(tempfile.mkdtemp(prefix=f"garden-bootstrap-{name}-"))
        archive = staging / pin["url"].rsplit("/", 1)[1]
        print(f"Installing {name} from {pin['url']}", flush=True)
        urllib.request.urlretrieve(pin["url"], archive)
        if hashlib.file_digest(archive.open("rb"), "sha256").hexdigest() != pin["sha256"]:
            raise RuntimeError(f"{name} checksum mismatch; nothing extracted")
        destination = tools / name
        if destination.exists():
            destination.rename(tools / f"{name}.previous-{staging.name}")
        destination.mkdir(parents=True)
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as package:
                package.extractall(destination)
        else:
            with tarfile.open(archive) as package:
                package.extractall(destination, filter="data")
        executable = next(destination.rglob(name + (".exe" if system == "windows" else "")))
        executable.chmod(0o755)
        if name == "node":
            # npm needs its files beside node; keep the full distribution intact.
            folder = executable.parent
        else:
            shutil.copy2(executable, bin_dir / executable.name)
            folder = bin_dir
        remember_path(folder, tools, system)
        subprocess.run([str(executable), "--version"], check=True)
    bunx = shutil.which("bunx")
    if bunx:
        remember_path(Path(bunx).parent, tools, system)
    else:
        bun = shutil.which("bun")
        launcher = bin_dir / ("bunx.exe" if system == "windows" else "bunx")
        if not launcher.exists():
            if system == "windows":
                shutil.copy2(bun, launcher)
            else:
                launcher.symlink_to(bun)
        remember_path(bin_dir, tools, system)
    print(f"Toolchains ready. Before running a demo, {docker_guidance(system)}")


if __name__ == "__main__":
    main()
