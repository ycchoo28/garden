"""The Variance read contract must describe tables and columns present after migrations."""

import re
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import get_settings
from app.shared.db import session_factory

BACKEND = Path(__file__).resolve().parents[2]
MODULES = BACKEND / "app/modules"
ROW = re.compile(r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")


def _contract(readme: Path) -> dict[str, set[str]]:
    lines = readme.read_text(encoding="utf-8").splitlines()
    header = next(
        (
            i
            for i, line in enumerate(lines)
            if "Owner" in line and "Table" in line and ROW.match(line)
        ),
        None,
    )
    if header is None:
        return {}
    result: dict[str, set[str]] = {}
    for line in lines[header + 2 :]:
        match = ROW.match(line)
        if not match:
            break
        _owner, table, columns, _meaning = (value.strip().strip("`") for value in match.groups())
        if "." in table:
            result[table] = {value.strip().strip("`") for value in columns.split(",")}
    return result


@pytest.mark.asyncio
async def test_variance_read_contract_matches_migrated_database(migrated_database: None) -> None:
    readmes = sorted([*MODULES.glob("*/README.md"), *(BACKEND / "app/core").glob("*/README.md")])
    contracts = {readme: _contract(readme) for readme in readmes}
    contracts = {readme: contract for readme, contract in contracts.items() if contract}
    assert contracts
    async with session_factory(get_settings().database_url)() as session:
        for readme, contract in contracts.items():
            for qualified_table, columns in contract.items():
                schema, table = qualified_table.split(".", 1)
                rows = await session.execute(
                    text(
                        "select column_name from information_schema.columns "
                        "where table_schema = :schema and table_name = :table"
                    ),
                    {"schema": schema, "table": table},
                )
                actual = {row.column_name for row in rows}
                assert actual, f"{qualified_table} is absent after migrations ({readme})"
                assert columns <= actual, (
                    f"{qualified_table} is missing declared columns: "
                    f"{sorted(columns - actual)} ({readme})"
                )
