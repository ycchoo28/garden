"""The downloads folder and the manifest: which files are offered, their SHA-256, and which names
reach a file in the folder. Downloads need no sign-in, and the folder is a temporary one."""

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.config import get_settings as real_get_settings
from app.core.agent.downloads import DownloadFile, download_path, offered_files, sha256_of
from app.shared.errors import NotFound


def test_offers_only_the_installer_files_that_are_present(tmp_path: Path) -> None:
    (tmp_path / "garden.pyz").write_bytes(b"abc")
    (tmp_path / "garden-linux").write_bytes(b"not offered")
    (tmp_path / ".hidden").write_bytes(b"secret")
    (tmp_path / "sub").mkdir()

    assert offered_files(tmp_path) == [
        DownloadFile(
            name="garden.pyz",
            size_bytes=3,
            sha256=hashlib.sha256(b"abc").hexdigest(),
        )
    ]


def test_a_missing_folder_offers_nothing(tmp_path: Path) -> None:
    assert offered_files(tmp_path / "missing") == []


@pytest.mark.parametrize("name", ["garden-linux", "../secret", "..", "a/b", ".hidden", ""])
def test_a_name_the_portal_does_not_offer_is_not_found(tmp_path: Path, name: str) -> None:
    (tmp_path / "garden-linux").write_bytes(b"not offered")

    with pytest.raises(NotFound):
        _ = download_path(tmp_path, name)


def test_a_link_that_leaves_the_folder_is_not_found(tmp_path: Path) -> None:
    root = tmp_path / "downloads"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"not for download")
    (root / "garden.pyz").symlink_to(outside)

    with pytest.raises(NotFound):
        _ = download_path(root, "garden.pyz")


def test_the_sha256_is_read_again_when_the_file_is_replaced(tmp_path: Path) -> None:
    path = tmp_path / "garden.pyz"
    path.write_bytes(b"first build")
    assert sha256_of(path) == hashlib.sha256(b"first build").hexdigest()

    path.write_bytes(b"a second, longer build")
    assert sha256_of(path) == hashlib.sha256(b"a second, longer build").hexdigest()


@pytest.fixture
def downloads_root(app: FastAPI, tmp_path: Path) -> Iterator[Path]:
    """The downloads folder is a temporary one, so the test controls what the portal offers."""
    settings = real_get_settings().model_copy(update={"agent_downloads_dir": tmp_path})
    app.dependency_overrides[real_get_settings] = lambda: settings
    yield tmp_path
    app.dependency_overrides.pop(real_get_settings, None)


@pytest.mark.asyncio
async def test_the_manifest_lists_each_offered_file_with_its_sha256(
    client: AsyncClient, downloads_root: Path
) -> None:
    (downloads_root / "garden.pyz").write_bytes(b"garden-1")
    (downloads_root / "install.sh").write_bytes(b"echo install")
    (downloads_root / "garden-linux").write_bytes(b"not offered")

    response = await client.get("/api/agent/manifest")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "artifacts": {
            "garden.pyz": {
                "url": "/api/agent/downloads/garden.pyz",
                "sha256": hashlib.sha256(b"garden-1").hexdigest(),
                "size_bytes": len(b"garden-1"),
            },
            "install.sh": {
                "url": "/api/agent/downloads/install.sh",
                "sha256": hashlib.sha256(b"echo install").hexdigest(),
                "size_bytes": len(b"echo install"),
            },
        }
    }


@pytest.mark.asyncio
async def test_a_download_is_served_without_a_sign_in(
    client: AsyncClient, downloads_root: Path
) -> None:
    (downloads_root / "dsh-config.yml").write_bytes(b"model: default")

    response = await client.get("/api/agent/downloads/dsh-config.yml")

    assert response.status_code == 200, response.text
    assert response.content == b"model: default"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["garden-linux", "..%2Fsecret", "%2e%2e", ".hidden"])
async def test_an_unknown_or_escaping_name_is_not_found(
    client: AsyncClient, downloads_root: Path, name: str
) -> None:
    (downloads_root / "garden-linux").write_bytes(b"not offered")
    (downloads_root / "secret").write_bytes(b"secret")

    response = await client.get(f"/api/agent/downloads/{name}")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
