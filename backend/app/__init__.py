"""Garden back end."""

import asyncio
import sys

# Psycopg's async connections require a selector loop on Windows.
# Apply this before seed, worker and queue CLI entry points call asyncio.run().
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
