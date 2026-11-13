"""Test code has one home, and product code never imports it (ADR 0029).

Product code is the agent package, the agent's build scripts and installers, and the backend's app
package. A `tests` folder inside those trees is test code and is exempt. Inside the tests, shared
test code imports neither the end-to-end run nor the evals.

Only absolute imports are checked. A relative import cannot reach a top-level `tests` package from
inside `garden` or `app`, so it never hits this rule.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
PRODUCT_PYTHON = ("agent/src", "agent/scripts", "backend/app")
INSTALLERS = "agent/install"
SHARED = "agent/tests/shared"
# A reference to the tests folder in an installer: a path such as `agent/tests/x.py` or
# `tests\x.py`, or a module such as `tests.e2e`. Installers never run test code.
INSTALLER_REFERENCE = re.compile(r"(?<!\w)tests[/\\.]\w")


def _under(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def absolute_imports(path: Path) -> list[tuple[int, str]]:
    """Every absolute module the file imports, with its line, including imports inside functions."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append((node.lineno, node.module))
    return found


def product_python_files(repo: Path) -> Iterator[Path]:
    for tree in PRODUCT_PYTHON:
        for path in sorted((repo / tree).rglob("*.py")):
            if "tests" not in path.relative_to(repo).parts:
                yield path


def product_violations(repo: Path) -> list[str]:
    violations: list[str] = []
    for path in product_python_files(repo):
        for line, name in absolute_imports(path):
            if _under(name, "tests"):
                violations.append(f"{path.relative_to(repo)}:{line} imports {name}")
    return violations


def installer_violations(repo: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted((repo / INSTALLERS).glob("*")):
        if path.suffix not in {".sh", ".ps1"}:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, text in enumerate(lines, start=1):
            if INSTALLER_REFERENCE.search(text):
                violations.append(f"{path.relative_to(repo)}:{number} refers to test code")
    return violations


def shared_violations(repo: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted((repo / SHARED).rglob("*.py")):
        for line, name in absolute_imports(path):
            if _under(name, "tests.e2e") or _under(name, "tests.evals"):
                violations.append(f"{path.relative_to(repo)}:{line} imports {name}")
    return violations


def test_product_code_never_imports_test_code() -> None:
    assert product_violations(REPO) == []


def test_installers_never_refer_to_test_code() -> None:
    assert installer_violations(REPO) == []


def test_shared_test_code_imports_neither_e2e_nor_evals() -> None:
    assert shared_violations(REPO) == []


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A repository with one file in each place the rules look at."""
    files = {
        "agent/src/garden/cli/app.py": "",
        "agent/scripts/build_zipapp.py": "",
        "agent/install/install.sh": "echo installed\n",
        "agent/tests/shared/stack.py": "",
        "agent/tests/e2e/run.py": "",
        "agent/tests/evals/grade.py": "",
        "backend/app/core/agent/service.py": "",
        "backend/app/modules/alpha/tests/test_fixture.py": "",
    }
    for relative, text in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def write(tree: Path, relative: str, code: str) -> None:
    (tree / relative).write_text(code, encoding="utf-8")


@pytest.mark.parametrize(
    ("relative", "code"),
    [
        ("agent/src/garden/cli/app.py", "from tests.shared.fake_portal import GOOD_TOKEN"),
        ("agent/src/garden/cli/app.py", "import tests.shared.stack"),
        ("agent/src/garden/cli/app.py", "def run():\n    from tests.shared.stack import api"),
        ("agent/scripts/build_zipapp.py", "from tests import e2e"),
        ("backend/app/core/agent/service.py", "from tests.fakes import FakeIdentity"),
        ("backend/app/core/agent/service.py", "import tests.support"),
    ],
)
def test_product_code_importing_test_code_fails(tree: Path, relative: str, code: str) -> None:
    write(tree, relative, code)
    violations = product_violations(tree)
    assert violations and relative in violations[0] and "tests" in violations[0]


def test_installer_referring_to_test_code_fails(tree: Path) -> None:
    write(tree, "agent/install/install.sh", "python -m tests.e2e.run\n")
    assert installer_violations(tree) == ["agent/install/install.sh:1 refers to test code"]
    write(tree, "agent/install/install.ps1", "Invoke-Expression agent\\tests\\run.ps1\n")
    assert len(installer_violations(tree)) == 2


@pytest.mark.parametrize(
    "code",
    [
        "from tests.e2e.run import main",
        "import tests.evals.grade",
        "def helper():\n    from tests.evals import grade",
    ],
)
def test_shared_test_code_importing_e2e_or_evals_fails(tree: Path, code: str) -> None:
    write(tree, "agent/tests/shared/stack.py", code)
    violations = shared_violations(tree)
    assert violations and "shared/stack.py" in violations[0]


@pytest.mark.parametrize(
    ("relative", "code"),
    [
        ("agent/src/garden/cli/app.py", "from garden.shared import Settings\nimport json"),
        ("agent/src/garden/cli/app.py", "tests = ['not an import']"),
        ("agent/scripts/build_zipapp.py", "from pathlib import Path"),
        ("backend/app/core/agent/service.py", "from app.shared.errors import ProblemError"),
        # Test folders inside product trees are test code and may import the test helpers.
        ("backend/app/modules/alpha/tests/test_fixture.py", "from tests.fakes import FakeIdentity"),
        ("agent/tests/e2e/run.py", "from tests.shared.stack import api"),
        ("agent/tests/evals/grade.py", "from tests.shared.transcript import Run"),
        ("agent/tests/shared/stack.py", "from tests.shared.transcript import Run"),
    ],
)
def test_permitted_imports_pass(tree: Path, relative: str, code: str) -> None:
    write(tree, relative, code)
    assert product_violations(tree) == []
    assert shared_violations(tree) == []


def test_installer_without_test_references_passes(tree: Path) -> None:
    write(tree, "agent/install/install.sh", "curl -fsSL https://example.test/install | sh\n")
    assert installer_violations(tree) == []
