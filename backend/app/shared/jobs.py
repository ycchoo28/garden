"""Background job queue on the same Postgres. The worker runs this same code in its own process."""

from functools import partial

import procrastinate
from psycopg_pool import AsyncConnectionPool

from app.config import get_settings
from app.shared.db import pin_session_to_utc

app = procrastinate.App(
    connector=procrastinate.PsycopgConnector(
        conninfo=get_settings().database_url.replace("postgresql+psycopg", "postgresql", 1),
        pool_factory=partial(AsyncConnectionPool, configure=pin_session_to_utc),
    ),
)
