"""Knowledge ingest through the API: access, the batch lifecycle, failed parses and preview ranking.

The background job runs in the test through `run_prepare_job` with the fake embedder. The queue
is replaced by a recorder, so the worker is not needed.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient

from app.modules.knowledge.batches.jobs import get_batch_queue, run_prepare_job
from app.modules.knowledge.reading.embedding import get_embedder
from tests.fakes import FakeEmbedder, FakeIdentity
from tests.support import sign_in

DEV_ADMIN = "dev.admin@fernhill.example"
LENA = "lena.fischer@fernhill.example"
ADMIN_ROLE = "admin"
NO_ROLES = "marcus.lee@fernhill.example"
MARKDOWN = "text/markdown"


class RecordingQueue:
    def __init__(self) -> None:
        self.batch_ids: list[uuid.UUID] = []

    async def enqueue_prepare(self, batch_id: uuid.UUID) -> None:
        self.batch_ids.append(batch_id)


@pytest_asyncio.fixture
async def queue(app: FastAPI) -> AsyncIterator[RecordingQueue]:
    recorder = RecordingQueue()
    app.dependency_overrides[get_batch_queue] = lambda: recorder
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    yield recorder
    _ = app.dependency_overrides.pop(get_batch_queue)
    _ = app.dependency_overrides.pop(get_embedder)


async def create_collection(
    client: AsyncClient, name: str, rules: Sequence[dict[str, object]]
) -> str:
    response = await client.post(
        "/api/knowledge/collections", json={"name": name, "rules": list(rules)}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def open_batch(client: AsyncClient, collection_id: str) -> str:
    response = await client.post(
        f"/api/knowledge/collections/{collection_id}/batches", json={"mode": "full_set"}
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


async def upload(
    client: AsyncClient,
    batch_id: str,
    name: str,
    data: bytes,
    *,
    content_type: str = MARKDOWN,
    translation_group: str | None = None,
) -> None:
    form = {"translation_group": translation_group} if translation_group else {}
    response = await client.post(
        f"/api/knowledge/batches/{batch_id}/files",
        files={"file": (name, data, content_type)},
        data=form,
    )
    assert response.status_code == 201, response.text


async def prepare(client: AsyncClient, queue: RecordingQueue, batch_id: str) -> None:
    """Start preparation as a person does, then run the background job as the worker would."""
    response = await client.post(f"/api/knowledge/batches/{batch_id}/prepare")
    assert response.status_code == 200, response.text
    assert response.json()["state"] == "preparing"
    assert queue.batch_ids[-1] == uuid.UUID(batch_id)
    await run_prepare_job(uuid.UUID(batch_id), embedder=FakeEmbedder())


async def preview(
    client: AsyncClient, batch_id: str, query: str, **extra: object
) -> dict[str, Any]:
    response = await client.post(
        f"/api/knowledge/batches/{batch_id}/preview", json={"query": query, **extra}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_only_an_admin_can_manage_collections_and_batches(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    await sign_in(client, identity, NO_ROLES)
    denied = await client.post("/api/knowledge/collections", json={"name": "Nope", "rules": []})
    assert denied.status_code == 403
    assert denied.headers["content-type"] == "application/problem+json"

    await sign_in(client, identity, DEV_ADMIN)
    collection_id = await create_collection(client, "Policies", [])
    await sign_in(client, identity, NO_ROLES)
    denied_batch = await client.post(
        f"/api/knowledge/collections/{collection_id}/batches", json={"mode": "full_set"}
    )
    assert denied_batch.status_code == 403


@pytest.mark.asyncio
async def test_a_collection_name_is_unique(client: AsyncClient, identity: FakeIdentity) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    _ = await create_collection(client, "Policies", [])
    again = await client.post("/api/knowledge/collections", json={"name": "Policies", "rules": []})
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_a_rule_must_name_its_reach(client: AsyncClient, identity: FakeIdentity) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    response = await client.post(
        "/api/knowledge/collections",
        json={"name": "Vague", "rules": [{"role": "manager"}]},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_preview_follows_the_collection_access_rules(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    everyone = await create_collection(
        client, "Everyone", [{"role": ADMIN_ROLE, "all_departments": True}]
    )
    design_only = await create_collection(
        client, "Design only", [{"role": ADMIN_ROLE, "department_codes": ["design"]}]
    )
    closed = await create_collection(client, "Closed", [])
    text = b"# Retention\n\nRetention policy keeps records for seven years."

    batches: dict[str, str] = {}
    for name, collection_id in (
        ("everyone", everyone),
        ("design", design_only),
        ("closed", closed),
    ):
        batch_id = await open_batch(client, collection_id)
        await upload(client, batch_id, f"{name}.md", text)
        await prepare(client, queue, batch_id)
        batches[name] = batch_id

    open_result = await preview(client, batches["everyone"], "retention policy")
    assert open_result["results"], "a collection readable by the caller should answer"

    # The caller is in operations, so a design-only rule and an empty rule list both fail closed.
    for name in ("design", "closed"):
        result = await preview(client, batches[name], "retention policy")
        assert result["results"] == [], name


@pytest.mark.asyncio
async def test_batch_lifecycle_is_enforced(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    collection_id = await create_collection(
        client, "Lifecycle", [{"role": ADMIN_ROLE, "all_departments": True}]
    )
    batch_id = await open_batch(client, collection_id)

    second = await client.post(
        f"/api/knowledge/collections/{collection_id}/batches", json={"mode": "partial"}
    )
    assert second.status_code == 409
    assert second.json()["code"] == "batch_in_progress"

    empty = await client.post(f"/api/knowledge/batches/{batch_id}/prepare")
    assert empty.status_code == 422

    early_preview = await client.post(
        f"/api/knowledge/batches/{batch_id}/preview", json={"query": "x"}
    )
    assert early_preview.status_code == 409

    await upload(client, batch_id, "lifecycle.md", b"# Lifecycle\n\nA batch moves through states.")
    await prepare(client, queue, batch_id)

    batch = (await client.get(f"/api/knowledge/batches/{batch_id}")).json()
    assert batch["state"] == "prepared"
    assert batch["prepared_at"] is not None

    late_upload = await client.post(
        f"/api/knowledge/batches/{batch_id}/files",
        files={"file": ("late.md", b"# Late\n\nToo late.", MARKDOWN)},
    )
    assert late_upload.status_code == 409


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_parsed_fails_the_batch(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    collection_id = await create_collection(
        client, "Broken", [{"role": ADMIN_ROLE, "all_departments": True}]
    )
    batch_id = await open_batch(client, collection_id)
    await upload(
        client, batch_id, "broken.pdf", b"%PDF-1.4 not really a pdf", content_type="application/pdf"
    )
    await upload(client, batch_id, "fine.md", b"# Fine\n\nThis file is readable.")
    await prepare(client, queue, batch_id)

    batch = (await client.get(f"/api/knowledge/batches/{batch_id}")).json()
    assert batch["state"] == "failed"
    assert batch["failure_reason"]
    failed = next(file for file in batch["files"] if file["name"] == "broken.pdf")
    assert failed["parse_status"] == "failed"
    assert failed["parse_error"]

    stuck = await client.post(
        f"/api/knowledge/batches/{batch_id}/preview", json={"query": "readable"}
    )
    assert stuck.status_code == 409


@pytest.mark.asyncio
async def test_preview_ranks_the_matching_document_first(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    collection_id = await create_collection(
        client, "Ranking", [{"role": ADMIN_ROLE, "all_departments": True}]
    )
    batch_id = await open_batch(client, collection_id)
    await upload(
        client,
        batch_id,
        "access.md",
        b"# Access review\n\nThe quarterly access review checks every account and role.",
    )
    await upload(
        client,
        batch_id,
        "backup.md",
        b"# Backup\n\nNightly backups are copied off site and restored each month.",
    )
    await prepare(client, queue, batch_id)

    result = await preview(client, batch_id, "quarterly access review")
    hits = result["results"]
    assert hits
    assert hits[0]["title"] == "Access review"
    assert hits[0]["heading_path"].startswith("Access review")
    assert hits[0]["locator"] == ""
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_translations_answer_once_in_the_query_language(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    collection_id = await create_collection(
        client, "Translations", [{"role": ADMIN_ROLE, "all_departments": True}]
    )
    batch_id = await open_batch(client, collection_id)
    await upload(
        client,
        batch_id,
        "policy-en.md",
        b"# Access policy\n\nAccess reviews run every quarter.",
        translation_group="policy",
    )
    await upload(
        client,
        batch_id,
        "policy-zh.md",
        "# 访问政策\n\n每季度进行一次访问审查。".encode(),
        translation_group="policy",
    )
    await prepare(client, queue, batch_id)

    english = await preview(client, batch_id, "access reviews every quarter")
    group_hits = [hit for hit in english["results"] if hit["translation_group"] == "policy"]
    assert len(group_hits) == 1
    assert group_hits[0]["language"] == "en"

    chinese = await preview(client, batch_id, "访问审查 每季度")
    group_hits = [hit for hit in chinese["results"] if hit["translation_group"] == "policy"]
    assert len(group_hits) == 1
    assert group_hits[0]["language"] == "zh"


@pytest.mark.asyncio
async def test_the_token_budget_marks_the_result_truncated(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    await sign_in(client, identity, DEV_ADMIN)
    collection_id = await create_collection(
        client, "Budget", [{"role": ADMIN_ROLE, "all_departments": True}]
    )
    batch_id = await open_batch(client, collection_id)
    long_body = " ".join(f"control{index}" for index in range(300))
    await upload(client, batch_id, "long.md", f"# Long\n\n{long_body}".encode())
    await prepare(client, queue, batch_id)

    result = await preview(client, batch_id, "control1 control2", max_tokens=200)
    assert result["results"] == []
    assert result["truncated"] is True
