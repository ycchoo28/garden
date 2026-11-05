"""A module talks to other modules only through its package interface (its __init__).

Feature folders sit inside a module, so a feature may import its sibling features by their package
too. Reaching into a file of another module, such as `app.modules.claims.ports`, is a violation.
Tests that live inside a module are exempt, because they build fixtures from other modules' parts.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
MODULE_GROUPS = ("core", "modules")


def _module_key(path: Path) -> tuple[str, str] | None:
    relative = path.relative_to(APP).parts
    if len(relative) >= 2 and relative[0] in MODULE_GROUPS:
        return relative[0], relative[1]
    return None


def _violations(path: Path) -> Iterator[str]:
    own = _module_key(path)
    if own is None or "tests" in path.relative_to(APP).parts:
        # Files outside the module folders, such as the composition root, may import anything.
        return
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or node.module is None:
            continue
        parts = node.module.split(".")
        if len(parts) < 3 or parts[0] != "app" or parts[1] not in MODULE_GROUPS:
            continue
        if (parts[1], parts[2]) == own:
            continue
        if len(parts) > 3:
            yield f"{path}:{node.lineno} imports {node.module}"
            continue
        for alias in node.names:
            if APP.joinpath(*parts[1:3], f"{alias.name}.py").is_file():
                yield f"{path}:{node.lineno} imports submodule {node.module}.{alias.name}"


def test_no_module_imports_another_modules_internals() -> None:
    violations = [message for path in APP.rglob("*.py") for message in _violations(path)]
    assert violations == []
