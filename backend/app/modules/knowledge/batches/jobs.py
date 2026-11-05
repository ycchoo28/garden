"""Background preparation of import batches.

The worker runs `prepare_batch_task`; tests run the same body.
"""

import uuid
from typing import Protocol

from app.config import get_settings
from app.core.audit import AuditRepository, get_audit_service
from app.core.files import FileRepository, get_file_service
from app.modules.knowledge.batches.ingest import prepare_batch
from app.modules.knowledge.batches.repository import BatchRepository
from app.modules.knowledge.reading.embedding import Embedder, get_embedder
from app.shared.clock import SystemClock
from app.shared.db import session_factory
from app.shared.jobs import app as jobs


class BatchQueue(Protocol):
    """Hands a batch to the background worker. The worker runs `prepare_batch`."""

    async def enqueue_prepare(self, batch_id: uuid.UUID) -> None: ...


class ProcrastinateBatchQueue:
    async def enqueue_prepare(self, batch_id: uuid.UUID) -> None:
        _ = await prepare_batch_task.defer_async(batch_id=str(batch_id))


def get_batch_queue() -> BatchQueue:
    return ProcrastinateBatchQueue()


async def run_prepare_job(batch_id: uuid.UUID, *, embedder: Embedder) -> None:
    """Open a session of its own, then prepare the batch.

    The worker and the tests both call this.
    """
    settings = get_settings()
    clock = SystemClock()
    async with session_factory(settings.database_url)() as session:
        audit = get_audit_service(AuditRepository(session), clock)
        files = get_file_service(FileRepository(session), audit, clock, settings)
        await prepare_batch(
            batch_id,
            repo=BatchRepository(session),
            files=files,
            audit=audit,
            embedder=embedder,
            clock=clock,
        )


@jobs.task(name="knowledge.prepare_batch", queue="slow")
async def prepare_batch_task(batch_id: str) -> None:
    await run_prepare_job(uuid.UUID(batch_id), embedder=get_embedder(get_settings()))
