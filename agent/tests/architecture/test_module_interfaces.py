"""An agent subpackage talks to other subpackages only through its public interface.

Reaching into an internal module of another subpackage is a violation.
Cross-subpackage imports must only import from the subpackage root.
"""

import ast
from collections.abc import Iterator
from pathlib import Path

GARDEN = Path(__file__).resolve().parents[2] / "src" / "garden"
SUBPACKAGES = ("shared", "auth", "engine", "dsh", "mcp", "cli")


def _subpackage(path: Path) -> str | None:
    try:
        relative = path.relative_to(GARDEN).parts
    except ValueError:
        return None
    if len(relative) >= 2 and relative[0] in SUBPACKAGES:
        return relative[0]
    return None


def _defines_all(init_path: Path) -> bool:
    if not init_path.is_file():
        return False
    tree = ast.parse(init_path.read_text(), filename=str(init_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    return True
        elif (
            isinstance(node, (ast.AnnAssign, ast.AugAssign))
            and isinstance(node.target, ast.Name)
            and node.target.id == "__all__"
        ):
            return True
    return False


def _resolve_import_module(path: Path, node: ast.ImportFrom) -> str | None:
    if node.level == 0:
        return node.module
    try:
        rel_parts = path.relative_to(GARDEN).parts
    except ValueError:
        return None
    pkg_parts = ("garden", *rel_parts[:-1])
    if node.level > len(pkg_parts):
        return None
    base_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)]
    if node.module:
        return ".".join(base_parts) + "." + node.module
    return ".".join(base_parts)


def _violations(path: Path) -> Iterator[str]:
    own = _subpackage(path)
    if own is None:
        return
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if (
                    len(parts) > 2
                    and parts[0] == "garden"
                    and parts[1] in SUBPACKAGES
                    and parts[1] != own
                ):
                    yield f"{path}:{node.lineno} imports {alias.name}"
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_module(path, node)
            if resolved is None:
                continue
            parts = resolved.split(".")
            if len(parts) < 2 or parts[0] != "garden" or parts[1] not in SUBPACKAGES:
                continue
            if parts[1] == own:
                continue
            if len(parts) > 2:
                yield f"{path}:{node.lineno} imports {resolved}"
                continue
            for alias in node.names:
                if (GARDEN / parts[1] / f"{alias.name}.py").is_file() or (
                    GARDEN / parts[1] / alias.name / "__init__.py"
                ).is_file():
                    yield f"{path}:{node.lineno} imports submodule {resolved}.{alias.name}"


def test_subpackages_define_all() -> None:
    missing: list[str] = []
    for subpkg in SUBPACKAGES:
        init_file = GARDEN / subpkg / "__init__.py"
        if not init_file.is_file():
            missing.append(f"{subpkg}/__init__.py is missing")
        elif not _defines_all(init_file):
            missing.append(f"{subpkg}/__init__.py does not define __all__")
    assert missing == []


def test_no_subpackage_imports_another_subpackages_internals() -> None:
    violations: list[str] = []
    for subpkg in SUBPACKAGES:
        subpkg_dir = GARDEN / subpkg
        if not subpkg_dir.is_dir():
            continue
        for path in sorted(subpkg_dir.rglob("*.py")):
            violations.extend(_violations(path))
    assert violations == []
