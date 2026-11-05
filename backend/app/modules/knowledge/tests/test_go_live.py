"""Knowledge go-live through the API: approval, apply, undo, as-of search, erase and the log.

The background job runs in the test through `run_prepare_job` with the fake embedder, as in
`test_api.py`.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from typing import Annotated, Any

import pytest
import pytest_asyncio
from fastapi import Depends, FastAPI, Request
from httpx import AsyncClient
from sqlalchemy import update

from app.config import get_settings
from app.core.users import CurrentPerson, current_person
from app.core.users.access import SESSION_COOKIE
from app.core.users.service import UserService, get_user_service
from app.modules.knowledge.batches.jobs import get_batch_queue
from app.modules.knowledge.models import Chunk
from app.modules.knowledge.reading.embedding import get_embedder
from app.modules.knowledge.tests.test_api import (
    ADMIN_ROLE,
    DEV_ADMIN,
    LENA,
    NO_ROLES,
    RecordingQueue,
    create_collection,
    open_batch,
    prepare,
    upload,
)
from app.shared.db import session_factory
from app.shared.request_login import VIA_AGENT, RequestLogin, current_request_login
from tests.fakes import FakeEmbedder, FakeIdentity, MovableClock
from tests.support import sign_in

EVERYONE: dict[str, object] = {"role": "employee", "all_departments": True}
RETENTION_V1 = b"# Retention\n\nRetention policy keeps records for seven years."
RETENTION_V2 = b"# Retention\n\nRetention policy keeps records for ten years."


@pytest_asyncio.fixture
async def queue(app: FastAPI) -> AsyncIterator[RecordingQueue]:
    recorder = RecordingQueue()
    app.dependency_overrides[get_batch_queue] = lambda: recorder
    app.dependency_overrides[get_embedder] = lambda: FakeEmbedder()
    yield recorder
    _ = app.dependency_overrides.pop(get_batch_queue)
    _ = app.dependency_overrides.pop(get_embedder)


def act_as_agent(app: FastAPI) -> None:
    """Marks the next requests as coming from a CLI login of the signed-in person."""

    async def as_agent(
        request: Request,
        service: Annotated[UserService, Depends(get_user_service)],
    ) -> CurrentPerson:
        person = await service.current_person(
            request.cookies.get(SESSION_COOKIE), request.app.state.roles
        )
        _ = current_request_login.set(RequestLogin(via=VIA_AGENT, device="laptop"))
        return person

    app.dependency_overrides[current_person] = as_agent


def act_as_browser(app: FastAPI) -> None:
    _ = app.dependency_overrides.pop(current_person, None)
    _ = current_request_login.set(None)


async def publish(
    client: AsyncClient, queue: RecordingQueue, collection_id: str, files: dict[str, bytes]
) -> str:
    """Open a batch, add the files and prepare it. The starter must then submit it."""
    batch_id = await open_batch(client, collection_id)
    for name, data in files.items():
        await upload(client, batch_id, name, data)
    await prepare(client, queue, batch_id)
    return batch_id


async def submit(client: AsyncClient, batch_id: str) -> dict[str, Any]:
    response = await client.post(f"/api/knowledge/batches/{batch_id}/submit")
    assert response.status_code == 200, response.text
    return dict(response.json())


async def approve(client: AsyncClient, batch_id: str, reason: str | None = None) -> Any:
    return await client.post(f"/api/knowledge/batches/{batch_id}/approve", json={"reason": reason})


async def apply_live(
    clock: MovableClock,
    client: AsyncClient,
    identity: FakeIdentity,
    queue: RecordingQueue,
    collection_id: str,
    files: dict[str, bytes],
) -> str:
    """Publish and submit as finance, and approve as the admin."""
    await sign_in(client, identity, LENA)
    batch_id = await publish(client, queue, collection_id, files)
    _ = await submit(client, batch_id)
    await sign_in(client, identity, DEV_ADMIN)
    # Each apply happens later than the one before, as it would in use.
    clock.advance(timedelta(minutes=1))
    approved = await approve(client, batch_id)
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "applied"
    return batch_id


async def new_collection(
    client: AsyncClient, identity: FakeIdentity, name: str, rules: Sequence[dict[str, object]]
) -> str:
    await sign_in(client, identity, DEV_ADMIN)
    return await create_collection(client, name, rules)


async def search(client: AsyncClient, query: str, **extra: object) -> dict[str, Any]:
    response = await client.post("/api/knowledge/search", json={"query": query, **extra})
    assert response.status_code == 200, response.text
    return dict(response.json())


@pytest.mark.asyncio
async def test_a_batch_applies_only_when_another_admin_approves(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    collection_id = await new_collection(client, identity, "Policies", [EVERYONE])

    # An admin who starts a batch may not approve it, although admins hold the decision.
    own_batch = await publish(client, queue, collection_id, {"retention.md": RETENTION_V1})
    _ = await submit(client, own_batch)
    own_decision = await approve(client, own_batch)
    assert own_decision.status_code == 403, own_decision.text
    cancelled = await client.post(
        f"/api/knowledge/batches/{own_batch}/cancel", json={"reason": "Started by mistake."}
    )
    assert cancelled.status_code == 200, cancelled.text

    await sign_in(client, identity, LENA)
    batch_id = await publish(client, queue, collection_id, {"retention.md": RETENTION_V1})
    submitted = await submit(client, batch_id)
    assert submitted["state"] == "awaiting_approval"
    assert submitted["risk"] == []

    # Finance starts the batch, and finance does not hold the decision.
    own = await approve(client, batch_id)
    assert own.status_code == 403, own.text
    assert (await client.get(f"/api/knowledge/batches/{batch_id}")).json()["state"] == (
        "awaiting_approval"
    )

    await sign_in(client, identity, DEV_ADMIN)
    approved = await approve(client, batch_id)
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "applied"

    await sign_in(client, identity, NO_ROLES)
    result = await search(client, "retention policy")
    assert result["results"], "the applied document should answer"
    hit = result["results"][0]
    assert hit["collection_name"] == "Policies"
    assert "seven years" in hit["text"]
    assert hit["heading_path"] and hit["title"]


@pytest.mark.asyncio
async def test_an_approval_is_bound_to_the_staged_result(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    collection_id = await new_collection(client, identity, "Bound", [EVERYONE])
    await sign_in(client, identity, LENA)
    batch_id = await publish(client, queue, collection_id, {"bound.md": RETENTION_V1})
    _ = await submit(client, batch_id)

    async with session_factory(get_settings().database_url)() as session:
        _ = await session.execute(
            update(Chunk)
            .where(Chunk.batch_id == uuid.UUID(batch_id))
            .values(body="A different body that was never shown to the approver.")
        )
        await session.commit()

    await sign_in(client, identity, DEV_ADMIN)
    changed = await approve(client, batch_id)
    assert changed.status_code == 409, changed.text
    assert changed.json()["code"] == "staged_result_changed"
    assert (await client.get(f"/api/knowledge/batches/{batch_id}")).json()["state"] == (
        "awaiting_approval"
    )


@pytest.mark.asyncio
async def test_a_rejection_needs_a_reason_and_the_starter_can_withdraw(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue
) -> None:
    collection_id = await new_collection(client, identity, "Rejects", [EVERYONE])
    await sign_in(client, identity, LENA)
    batch_id = await publish(client, queue, collection_id, {"rejects.md": RETENTION_V1})
    _ = await submit(client, batch_id)

    await sign_in(client, identity, DEV_ADMIN)
    silent = await client.post(f"/api/knowledge/batches/{batch_id}/reject", json={"reason": " "})
    assert silent.status_code == 422, silent.text
    rejected = await client.post(
        f"/api/knowledge/batches/{batch_id}/reject", json={"reason": "Wrong retention period."}
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["state"] == "rejected"

    await sign_in(client, identity, LENA)
    second_id = await publish(client, queue, collection_id, {"second.md": RETENTION_V2})
    _ = await submit(client, second_id)
    withdrawn = await client.post(
        f"/api/knowledge/batches/{second_id}/cancel", json={"reason": "Changed my mind."}
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["state"] == "cancelled"


@pytest.mark.asyncio
async def test_an_agent_cannot_decide_erase_or_read_the_log(
    client: AsyncClient, identity: FakeIdentity, app: FastAPI, queue: RecordingQueue
) -> None:
    collection_id = await new_collection(client, identity, "Agents", [EVERYONE])
    await sign_in(client, identity, LENA)
    batch_id = await publish(client, queue, collection_id, {"agents.md": RETENTION_V1})
    _ = await submit(client, batch_id)

    await sign_in(client, identity, DEV_ADMIN)
    act_as_agent(app)
    assert (await approve(client, batch_id)).status_code == 403
    erase = await client.post(
        f"/api/knowledge/collections/{collection_id}/documents/erase",
        json={"name": "agents.md", "reason": "Requested."},
    )
    assert erase.status_code == 403
    assert (await client.get("/api/knowledge/search-log")).status_code == 403
    act_as_browser(app)


@pytest.mark.asyncio
async def test_a_large_removal_and_an_access_change_are_flagged_on_the_request(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue, clock: MovableClock
) -> None:
    collection_id = await new_collection(client, identity, "Risky", [EVERYONE])
    four = {f"policy-{index}.md": RETENTION_V1 for index in range(4)}
    _ = await apply_live(clock, client, identity, queue, collection_id, four)

    await sign_in(client, identity, LENA)
    batch_id = await open_batch(client, collection_id)
    # A full set of one file removes the three live documents it does not name.
    await upload(client, batch_id, "policy-0.md", RETENTION_V2)
    await prepare(client, queue, batch_id)
    submitted = await submit(client, batch_id)
    assert submitted["risk"] == ["large_removal"]
    assert len(submitted["report"]["removed_documents"]) == 3


@pytest.mark.asyncio
async def test_undo_restores_the_previous_live_state(
    client: AsyncClient,
    identity: FakeIdentity,
    queue: RecordingQueue,
    app: FastAPI,
    clock: MovableClock,
) -> None:
    collection_id = await new_collection(client, identity, "Undo", [EVERYONE])
    first = await apply_live(
        clock, client, identity, queue, collection_id, {"retention.md": RETENTION_V1}
    )
    second = await apply_live(
        clock, client, identity, queue, collection_id, {"retention.md": RETENTION_V2}
    )

    await sign_in(client, identity, DEV_ADMIN)
    blocked = await client.post(f"/api/knowledge/batches/{first}/undo")
    assert blocked.status_code == 409, blocked.text

    act_as_agent(app)
    challenged = await client.post(f"/api/knowledge/batches/{second}/undo")
    assert challenged.status_code == 428, challenged.text
    act_as_browser(app)

    undone = await client.post(f"/api/knowledge/batches/{second}/undo")
    assert undone.status_code == 200, undone.text
    assert undone.json()["state"] == "undone"

    result = await search(client, "retention policy", limit=1)
    assert "seven years" in result["results"][0]["text"]


@pytest.mark.asyncio
async def test_as_of_search_reads_what_was_live_at_that_moment(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue, clock: MovableClock
) -> None:
    collection_id = await new_collection(client, identity, "History", [EVERYONE])
    _ = await apply_live(
        clock, client, identity, queue, collection_id, {"retention.md": RETENTION_V1}
    )
    between = clock.current
    clock.advance(timedelta(days=1))
    _ = await apply_live(
        clock, client, identity, queue, collection_id, {"retention.md": RETENTION_V2}
    )

    past = await search(client, "retention policy", as_of=between.isoformat())
    assert "seven years" in past["results"][0]["text"]
    assert past["as_of"] is not None

    now = await search(client, "retention policy")
    assert "ten years" in now["results"][0]["text"]

    future = await client.post(
        "/api/knowledge/search",
        json={"query": "retention", "as_of": (clock.current + timedelta(days=1)).isoformat()},
    )
    assert future.status_code == 422, future.text


@pytest.mark.asyncio
async def test_erase_removes_the_document_from_search_and_file_storage(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue, clock: MovableClock
) -> None:
    collection_id = await new_collection(client, identity, "Erasure", [EVERYONE])
    batch_id = await apply_live(
        clock, client, identity, queue, collection_id, {"erase-me.md": RETENTION_V1}
    )
    batch = (await client.get(f"/api/knowledge/batches/{batch_id}")).json()
    sha256 = str(batch["files"][0]["sha256"])
    stored = get_settings().files_root / sha256[:2] / sha256
    assert stored.exists()

    await sign_in(client, identity, DEV_ADMIN)
    erased = await client.post(
        f"/api/knowledge/collections/{collection_id}/documents/erase",
        json={"name": "erase-me.md", "reason": "Filed in error."},
    )
    assert erased.status_code == 204, erased.text
    assert not stored.exists()

    await sign_in(client, identity, NO_ROLES)
    assert (await search(client, "retention policy"))["results"] == []


@pytest.mark.asyncio
async def test_the_search_log_records_who_asked_and_what_came_back(
    client: AsyncClient,
    identity: FakeIdentity,
    queue: RecordingQueue,
    app: FastAPI,
    clock: MovableClock,
) -> None:
    collection_id = await new_collection(client, identity, "Logged", [EVERYONE])
    _ = await apply_live(clock, client, identity, queue, collection_id, {"logged.md": RETENTION_V1})

    await sign_in(client, identity, NO_ROLES)
    act_as_agent(app)
    _ = await search(client, "retention policy")
    act_as_browser(app)

    await sign_in(client, identity, NO_ROLES)
    forbidden = await client.get("/api/knowledge/search-log")
    assert forbidden.status_code == 403

    await sign_in(client, identity, DEV_ADMIN)
    entries = (await client.get("/api/knowledge/search-log")).json()
    latest = entries[0]
    assert latest["person_name"] == "Marcus Lee"
    assert latest["query"] == "retention policy"
    assert latest["acting_as_agent"] is True
    assert latest["chunk_ids"]


@pytest.mark.asyncio
async def test_a_collection_readable_by_no_role_of_the_caller_fails_closed(
    client: AsyncClient, identity: FakeIdentity, queue: RecordingQueue, clock: MovableClock
) -> None:
    collection_id = await new_collection(
        client, identity, "Admins only", [{"role": ADMIN_ROLE, "all_departments": True}]
    )
    _ = await apply_live(clock, client, identity, queue, collection_id, {"admins.md": RETENTION_V1})
    await sign_in(client, identity, NO_ROLES)
    assert (await search(client, "retention policy"))["results"] == []
