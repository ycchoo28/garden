"""Alembic environment. One database, one history, one schema per module."""

from collections.abc import MutableMapping
from typing import Literal

from alembic import context
from sqlalchemy import create_engine

from app.composition import MODULES
from app.config import get_settings

SCHEMAS = frozenset(module.name for module in MODULES)


ParentKey = Literal["schema_name", "table_name", "schema_qualified_table_name"]


def include_name(
    name: str | None, type_: str, parent_names: MutableMapping[ParentKey, str | None]
) -> bool:
    """Autogenerate compares only the module schemas, never the queue's or Alembic's tables."""
    if type_ == "schema":
        return name in SCHEMAS
    if type_ == "table":
        return parent_names.get("schema_name") in SCHEMAS
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().database_url,
        target_metadata=[module.metadata for module in MODULES],
        include_schemas=True,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=[module.metadata for module in MODULES],
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
