"""Exercise reporting contract checks through their contributor-facing command."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts/check_reporting_contracts.py"


def _tree(tmp_path: Path, query: str) -> Path:
    shutil.copy(CHECK, tmp_path / "check_reporting_contracts.py")
    repo = tmp_path / "backend/app/modules/budgets/variance/repository.py"
    repo.parent.mkdir(parents=True)
    repo.write_text(
        f"from sqlalchemy import text as sql_text\n"
        f'QUERY = f"""{query}"""\n'
        "STATEMENT = sql_text(QUERY)\n",
        encoding="utf-8",
    )
    readme = tmp_path / "backend/app/modules/budgets/README.md"
    readme.parent.mkdir(parents=True, exist_ok=True)
    readme.write_text(
        "| Owner | Table | Columns read | Meaning used |\n"
        "| --- | --- | --- | --- |\n"
        "| Claims drafting | `claims.claim` | `id`, `department_id` | Claim scope. |\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(tree: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(tree / "check_reporting_contracts.py"), "--root", str(tree)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_reporting_contract_allows_owned_and_declared_reads(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "select c.id from budgets.line b join claims.claim c on c.id = b.id")
    result = _run(tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reporting_contract_covers_comma_list_and_only_reads(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "select c.id from budgets.line b, only claims.claim c")
    result = _run(tree)
    assert result.returncode == 0, result.stdout + result.stderr


def test_reporting_contract_rejects_comma_list_and_only_foreign_reads(
    tmp_path: Path,
) -> None:
    tree = _tree(tmp_path, "select c.id from budgets.line b, only claims.private c")
    result = _run(tree)
    assert result.returncode == 1
    assert "reporting-read-contract" in result.stdout
    assert "claims.private" in result.stdout


def test_reporting_contract_rejects_undeclared_foreign_read(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "select x.id from claims.private x")
    result = _run(tree)
    assert result.returncode == 1
    assert "reporting-read-contract" in result.stdout
    assert "repository.py:3" in result.stdout


def test_reporting_contract_rejects_unmodeled_schema_read(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "select x.id from future_schema.private x")
    result = _run(tree)
    assert result.returncode == 1
    assert "reporting-read-contract" in result.stdout
    assert "future_schema.private" in result.stdout


def test_reporting_contract_rejects_foreign_write(tmp_path: Path) -> None:
    tree = _tree(tmp_path, "update claims.claim set department_id = 'x'")
    result = _run(tree)
    assert result.returncode == 1
    assert "reporting-foreign-write" in result.stdout
    assert "claims.claim" in result.stdout


@pytest.mark.parametrize(
    "statement",
    [
        "truncate table only budgets.line, claims.claim",
        "drop table only budgets.line, claims.claim",
        "alter table only claims.claim add column changed_at timestamp",
        "create table claims.new_table (id uuid)",
    ],
)
def test_reporting_contract_rejects_foreign_truncate_and_ddl_targets(
    tmp_path: Path, statement: str
) -> None:
    tree = _tree(tmp_path, statement)
    result = _run(tree)
    assert result.returncode == 1
    assert "reporting-foreign-write" in result.stdout
    assert "claims." in result.stdout


def test_reporting_contract_allows_owned_truncate_and_ignores_sql_literals(
    tmp_path: Path,
) -> None:
    tree = _tree(
        tmp_path,
        "truncate table only budgets.line; select 'from claims.private' as example",
    )
    result = _run(tree)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("marker", ["--", "/*"])
def test_literal_comment_markers_do_not_hide_foreign_relations(tmp_path: Path, marker: str) -> None:
    tree = _tree(tmp_path, f"select '{marker}' from claims.private")
    result = _run(tree)
    assert result.returncode == 1
    assert "reporting-read-contract" in result.stdout
    assert "claims.private" in result.stdout


def test_repository_reporting_contracts_are_declared() -> None:
    result = subprocess.run(
        [sys.executable, str(CHECK)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
