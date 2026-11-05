"""Build a dsh bundle: dsh with its locked npm dependencies, unpacked by the installer.

The bundle needs Node.js on the laptop, not npm: `npm ci` runs here, once, from the lockfile in
install/dsh.
The archive holds `node_modules` and `package.json` at its top level, so it unpacks into a folder of
its own. Packages with native code are built for one platform, so each target has its own bundle:

- `linux-x64` (the default): built on this machine, which must be linux x86_64. Output
  `dist/dsh-bundle.tar.gz`, the file the installers have always fetched.
- `windows-x64`: built on any machine, with npm told to take the Windows x64 optional packages.
  Output `dist/dsh-bundle-windows-x64.tar.gz`, which install.ps1 fetches.

Usage: python scripts/build_dsh_bundle.py [--target linux-x64|windows-x64] [OUTPUT]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tarfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "install" / "dsh"
DIST = ROOT / "dist"

# Each target: the file the portal serves, and the npm flags that pick that platform's packages.
TARGETS: dict[str, tuple[str, list[str]]] = {
    "linux-x64": ("dsh-bundle.tar.gz", []),
    "windows-x64": ("dsh-bundle-windows-x64.tar.gz", ["--os=win32", "--cpu=x64"]),
}


def build(output: Path, target: str) -> Path:
    staging = DIST / f"dsh-staging-{target}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name in ("package.json", "package-lock.json"):
        shutil.copy2(MANIFEST_DIR / name, staging / name)
    # Lifecycle scripts stay off: the bundle must not run code when the laptop unpacks it.
    subprocess.run(
        [
            "npm",
            "ci",
            "--omit=dev",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
            *TARGETS[target][1],
        ],
        cwd=staging,
        check=True,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w:gz", compresslevel=9) as archive:
        archive.add(staging / "package.json", arcname="package.json")
        archive.add(staging / "node_modules", arcname="node_modules", filter=_portable_only(target))
    shutil.rmtree(staging)
    return output


def _portable_only(target: str) -> Callable[[tarfile.TarInfo], tarfile.TarInfo | None] | None:
    """For Windows, leave out the symlinks npm makes in node_modules/.bin.

    tar.exe on Windows cannot create symlinks without developer mode, and dsh runs from the package
    files, not from those shims. Linux keeps them, as it always has.
    """
    if target != "windows-x64":
        return None

    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        return None if info.issym() or info.islnk() else info

    return keep


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", choices=sorted(TARGETS), default="linux-x64")
    parser.add_argument("output", nargs="?", type=Path, help="where to write the bundle")
    args = parser.parse_args()
    output = args.output or DIST / TARGETS[args.target][0]
    path = build(output.resolve(), args.target)
    megabytes = path.stat().st_size / (1024 * 1024)
    print(f"Wrote {path} ({megabytes:.1f} MB, compressed, {args.target}).")


if __name__ == "__main__":
    main()
