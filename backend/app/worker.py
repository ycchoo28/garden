"""Background worker: `uv run python -m app.worker`. It runs the same code as the API."""

import asyncio
import logging

from app.modules.knowledge.batches import jobs as knowledge_jobs
from app.shared.jobs import app as jobs

# Importing a module's jobs registers its tasks with the worker.
_TASK_MODULES = (knowledge_jobs,)


async def main() -> None:
    # Procrastinate logs "Starting worker" at INFO, so the browser tests can wait for it on stderr.
    logging.basicConfig(level=logging.INFO)
    async with jobs.open_async():
        await jobs.run_worker_async(queues=["fast", "slow"])


if __name__ == "__main__":
    asyncio.run(main())
