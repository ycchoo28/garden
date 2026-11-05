"""Files: content addressing, size and type limits, and the type the server decides on."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.config import Settings
from app.config import get_settings as real_get_settings
from app.core.files.service import sniff_content_type
from tests.fakes import FakeIdentity
from tests.support import sign_in

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_sniffing_recognises_only_allowed_types() -> None:
    assert sniff_content_type(b"%PDF-1.7 body") == "application/pdf"
    assert sniff_content_type(PNG) == "image/png"
    assert sniff_content_type(b"\xff\xd8\xff\xe0 jpeg") == "image/jpeg"
    assert sniff_content_type(b"plain words") == "text/plain"
    assert sniff_content_type(b"MZ\x90\x00 executable") is None
    assert sniff_content_type(b"text\x00with a nul") is None


@pytest.fixture
def small_store(app: FastAPI, tmp_path: Path) -> Iterator[Settings]:
    """Settings with a private files root and a 1 KiB limit, so the limit tests stay small."""
    settings = real_get_settings().model_copy(
        update={"files_root": tmp_path, "files_max_bytes": 1024}
    )
    app.dependency_overrides[real_get_settings] = lambda: settings
    yield settings
    del app.dependency_overrides[real_get_settings]


@pytest.mark.asyncio
async def test_identical_uploads_share_one_stored_file(
    client: AsyncClient, identity: FakeIdentity, small_store: Settings
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    first = await client.post("/api/files", files={"file": ("a.png", PNG, "image/png")})
    second = await client.post("/api/files", files={"file": ("b.png", PNG, "image/png")})
    assert first.status_code == second.status_code == 200
    assert first.json()["sha256"] == second.json()["sha256"]
    stored = small_store.files_root / first.json()["sha256"][:2] / first.json()["sha256"]
    assert stored.read_bytes() == PNG


@pytest.mark.asyncio
async def test_oversized_upload_is_refused(
    client: AsyncClient, identity: FakeIdentity, small_store: Settings
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    big = b"a" * 2048
    response = await client.post("/api/files", files={"file": ("big.txt", big, "text/plain")})
    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")


@pytest.mark.asyncio
async def test_disallowed_type_is_refused_whatever_the_client_claims(
    client: AsyncClient, identity: FakeIdentity, small_store: Settings
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    response = await client.post(
        "/api/files", files={"file": ("notes.png", b"MZ\x90\x00 binary", "image/png")}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_download_returns_the_bytes_with_safe_headers(
    client: AsyncClient, identity: FakeIdentity, small_store: Settings
) -> None:
    await sign_in(client, identity, "marcus.lee@fernhill.example")
    uploaded = await client.post("/api/files", files={"file": ("a.png", PNG, "image/png")})
    sha = uploaded.json()["sha256"]
    downloaded = await client.get(f"/api/files/{sha}")
    assert downloaded.status_code == 200
    assert downloaded.content == PNG
    assert downloaded.headers["content-type"] == "image/png"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert (await client.get("/api/files/not-a-hash")).status_code == 404


@pytest.mark.asyncio
async def test_upload_needs_a_sign_in(client: AsyncClient) -> None:
    response = await client.post("/api/files", files={"file": ("a.txt", b"hi", "text/plain")})
    assert response.status_code == 401
