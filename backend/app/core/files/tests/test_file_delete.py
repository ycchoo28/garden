"""Deleting a file is critical: an agent waits for the person to approve it in the browser."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.config import Settings
from app.config import get_settings as real_get_settings
from app.core.agent import CONFIRMATION_HEADER
from tests.fakes import FakeIdentity
from tests.support import bearer, cli_sign_in, sign_in

MARCUS = "marcus.lee@fernhill.example"
PRIYA = "priya.nair@fernhill.example"
PDF = b"%PDF-1.7 a document an agent wants gone"


@pytest.fixture
def store(app: FastAPI, tmp_path: Path) -> Iterator[Settings]:
    settings = real_get_settings().model_copy(update={"files_root": tmp_path})
    app.dependency_overrides[real_get_settings] = lambda: settings
    yield settings
    del app.dependency_overrides[real_get_settings]


async def _upload(client: AsyncClient, token: str) -> str:
    uploaded = await client.post(
        "/api/files",
        files={"file": ("plan.pdf", PDF, "application/pdf")},
        headers=bearer(token),
    )
    assert uploaded.status_code == 200, uploaded.text
    return str(uploaded.json()["sha256"])


@pytest.mark.asyncio
async def test_agent_delete_waits_for_the_person_then_runs_once(
    client: AsyncClient, identity: FakeIdentity, store: Settings
) -> None:
    token = await cli_sign_in(client, identity, MARCUS)
    sha = await _upload(client, token)
    blob = store.files_root / sha[:2] / sha

    challenged = await client.delete(f"/api/files/{sha}", headers=bearer(token))
    assert challenged.status_code == 428, challenged.text
    problem = challenged.json()
    assert problem["confirmation_url"].endswith(
        f"/agent/confirmations/{problem['confirmation_id']}"
    )
    assert blob.is_file()

    await sign_in(client, identity, MARCUS)
    decided = await client.post(
        f"/api/agent/my-confirmations/{problem['confirmation_id']}/decision",
        json={"decision": "approve"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["summary"].startswith(f"DELETE /api/files/{sha}")

    deleted = await client.delete(
        f"/api/files/{sha}",
        headers={**bearer(token), CONFIRMATION_HEADER: problem["confirmation_id"]},
    )
    assert deleted.status_code == 204, deleted.text
    assert not blob.exists()

    await sign_in(client, identity, "lena.fischer@fernhill.example")
    events = (await client.get("/api/audit/events")).json()
    [removed] = [event for event in events if event["operation"] == "files.file.delete"]
    assert removed["acting_as_agent"] is True
    assert removed["confirmation_id"] == problem["confirmation_id"]
    assert removed["confirmed_at"] is not None

    replayed = await client.delete(
        f"/api/files/{sha}",
        headers={**bearer(token), CONFIRMATION_HEADER: problem["confirmation_id"]},
    )
    assert replayed.status_code == 409


@pytest.mark.asyncio
async def test_only_the_first_uploader_may_delete_the_file(
    client: AsyncClient, identity: FakeIdentity, store: Settings
) -> None:
    token = await cli_sign_in(client, identity, MARCUS)
    sha = await _upload(client, token)
    await sign_in(client, identity, PRIYA)
    refused = await client.delete(f"/api/files/{sha}")
    assert refused.status_code == 403
    assert (store.files_root / sha[:2] / sha).is_file()
