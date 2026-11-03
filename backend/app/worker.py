"""Background worker: `uv run python -m app.worker`. It runs the same code as the API."""

import asyncio

from app.shared.jobs import app as jobs


async def main() -> None:
    async with jobs.open_async():
        await jobs.run_worker_async(queues=["fast", "slow"])


if __name__ == "__main__":
    asyncio.run(main())
