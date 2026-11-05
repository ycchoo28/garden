"""The files the installer and the get-started page offer: the garden CLI, dsh and the installers.

Only the names in PUBLIC_DOWNLOADS are offered, from the configured folder. The installer fetches
them before anyone signs in, so they hold no secrets. Any other file in the folder is never served,
and a request cannot reach anything outside the folder.
"""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.shared.errors import NotFound

# A file name: letters, digits, dots, dashes and underscores, not starting with a dot.
FILE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# The files the portal offers. Each one is read-only and holds no secret. The dsh bundle is per
# platform: dsh-bundle.tar.gz is linux x86_64, and dsh-bundle-windows-x64.tar.gz is Windows x64.
PUBLIC_DOWNLOADS: frozenset[str] = frozenset(
    {
        "garden.pyz",
        "dsh-bundle.tar.gz",
        "dsh-bundle-windows-x64.tar.gz",
        "dsh-config.yml",
        "install.sh",
        "install.ps1",
    }
)


@dataclass(frozen=True)
class DownloadFile:
    name: str
    size_bytes: int
    sha256: str


def offered_files(root: Path) -> list[DownloadFile]:
    """The offered files that are present in the folder, by name. A missing folder has none."""
    if not root.is_dir():
        return []
    return [
        DownloadFile(
            name=name,
            size_bytes=(root / name).stat().st_size,
            sha256=sha256_of(root / name),
        )
        for name in sorted(PUBLIC_DOWNLOADS)
        if (root / name).is_file()
    ]


def download_path(root: Path, name: str) -> Path:
    """The file to send for a name, or NotFound when the portal does not offer that file."""
    if name not in PUBLIC_DOWNLOADS or not FILE_NAME.match(name):
        raise NotFound("There is no such download.")
    path = root / name
    if not path.is_file() or path.resolve().parent != root.resolve():
        raise NotFound("There is no such download.")
    return path


def sha256_of(path: Path) -> str:
    """The SHA-256 of a file. The result is kept while the file keeps its size and modified time."""
    stat = path.stat()
    return _sha256_cached(str(path), stat.st_size, stat.st_mtime_ns)


@lru_cache(maxsize=32)
def _sha256_cached(path: str, size: int, modified_ns: int) -> str:
    # The size and the modified time are in the key, so a replaced file is hashed again.
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
