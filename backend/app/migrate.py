"""Run the migrations: `python -m app.migrate`, which `just migrate` and the browser tests call."""

import subprocess
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    command.upgrade(Config(str(BACKEND_ROOT / "alembic.ini")), "heads")
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        queue_exists = connection.execute(
            text("select to_regclass('public.procrastinate_jobs') is not null")
        ).scalar_one()
    engine.dispose()
    if not queue_exists:
        _ = subprocess.run(
            [
                sys.executable,
                "-m",
                "procrastinate",
                "--app=app.shared.jobs.app",
                "schema",
                "--apply",
            ],
            check=True,
        )


if __name__ == "__main__":
    main()
