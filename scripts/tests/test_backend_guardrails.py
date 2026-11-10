"""Exercise the contributor command against permitted and prohibited imports."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts/check_backend_architecture.py", tmp_path / "scripts")
    for package in (
        "app",
        "app.modules",
        "app.core",
        "app.shared",
        "app.modules.alpha",
        "app.modules.beta",
        "app.modules.alpha.first",
        "app.modules.alpha.second",
    ):
        folder = tmp_path / "backend" / Path(*package.split("."))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "__init__.py").write_text("")
    for module in (
        "app.modules.beta.service",
        "app.modules.alpha.first.repository",
        "app.shared.db",
    ):
        (tmp_path / "backend" / Path(*module.split("."))).with_suffix(".py").write_text("")
    return tmp_path


def run(tree: Path, source: str, code: str) -> subprocess.CompletedProcess[str]:
    (tree / "backend" / source).write_text(code)
    return subprocess.run(
        [sys.executable, str(tree / "scripts/check_backend_architecture.py")],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "code",
    [
        "from app.modules.beta.service import thing",
        "import app.modules.beta.service",
        "from ..beta.service import thing",
        "def call():\n    from app.modules.beta.service import thing",
        "from app.modules.beta import service",
    ],
)
def test_private_import_forms(tree: Path, code: str) -> None:
    result = run(tree, "app/modules/alpha/service.py", code)
    assert result.returncode != 0
    assert "module-interface" in result.stdout and "alpha/service.py:" in result.stdout
    permitted = run(tree, "app/modules/alpha/service.py", "from app.modules.beta import thing")
    assert permitted.returncode == 0, permitted.stdout + permitted.stderr


def test_repository_privacy(tree: Path) -> None:
    code = "from app.modules.alpha.first.repository import Thing"
    bad = run(tree, "app/modules/alpha/second/service.py", code)
    assert bad.returncode != 0 and "feature-repository" in bad.stdout
    (tree / "backend/app/modules/alpha/second/service.py").write_text("")
    good = run(tree, "app/modules/alpha/first/service.py", code)
    assert good.returncode == 0, good.stdout


@pytest.mark.parametrize("code", ["from app.shared.db import session", "import psycopg"])
def test_database_access(tree: Path, code: str) -> None:
    bad = run(tree, "app/modules/alpha/helper.py", code)
    assert bad.returncode != 0 and "database-layer" in bad.stdout
    (tree / "backend/app/modules/alpha/helper.py").write_text("")
    good = run(tree, "app/modules/alpha/first/repository.py", code)
    assert good.returncode == 0, good.stdout


def test_cycle(tree: Path) -> None:
    run(tree, "app/modules/alpha/service.py", "from app.modules.beta import thing")
    bad = run(tree, "app/modules/beta/service.py", "from app.modules.alpha import thing")
    assert bad.returncode != 0 and "module-cycle" in bad.stdout
    good = run(tree, "app/modules/beta/service.py", "")
    assert good.returncode == 0, good.stdout


def test_module_tests_are_exempt(tree: Path) -> None:
    folder = tree / "backend/app/modules/alpha/tests"
    folder.mkdir()
    (folder / "__init__.py").write_text("")
    result = run(
        tree,
        "app/modules/alpha/tests/test_fixture.py",
        "from app.modules.beta.service import thing",
    )
    assert result.returncode == 0, result.stdout


@pytest.mark.skipif(
    not (ROOT / "backend/app/modules/ciproof/api.py").is_file(),
    reason="CI Scaffold generates ciproof before running this proof",
)
@pytest.mark.parametrize(
    "code,rule",
    [
        ("import app.modules.claims.drafting.service", "module-interface"),
        ("from ..claims.drafting import service", "module-interface"),
        ("def planted():\n    from app.shared.db import get_session", "database-layer"),
        (
            "from app.modules.budgets.variance.repository import VarianceRepository",
            "feature-repository",
        ),
    ],
)
def test_generated_scaffold_rejects_violation(code: str, rule: str) -> None:
    path = ROOT / "backend/app/modules/ciproof/api.py"
    original = path.read_text()
    command = [sys.executable, str(ROOT / "scripts/check_backend_architecture.py")]
    try:
        path.write_text(original + "\n" + code + "\n")
        bad = subprocess.run(command, capture_output=True, text=True, check=False)
        assert bad.returncode != 0 and rule in bad.stdout, bad.stdout + bad.stderr
        assert "ciproof/api.py:" in bad.stdout
    finally:
        path.write_text(original)
    good = subprocess.run(command, capture_output=True, text=True, check=False)
    assert good.returncode == 0, good.stdout + good.stderr


@pytest.mark.parametrize(
    "source,code",
    [
        ("app/core/service.py", "from app.modules.alpha import thing"),
        ("app/shared/helper.py", "from app.core import thing"),
        ("app/shared/helper.py", "from app.modules.alpha import thing"),
    ],
)
def test_foundation_import_contracts(tree: Path, source: str, code: str) -> None:
    import os

    shutil.copy(ROOT / "backend/pyproject.toml", tree / "backend")
    path = tree / "backend" / source
    path.write_text(code)
    command = [str(ROOT / "backend/.venv/bin/lint-imports"), "--no-cache"]
    environment = {**os.environ, "PYTHONPATH": str(tree / "backend")}
    bad = subprocess.run(
        command,
        cwd=tree / "backend",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode != 0 and "BROKEN" in bad.stdout, bad.stdout + bad.stderr
    path.write_text("")
    good = subprocess.run(
        command,
        cwd=tree / "backend",
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert good.returncode == 0, good.stdout + good.stderr
