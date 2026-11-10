"""Agent package boundaries and the published operation names used by skills."""

import ast
import json
import re
import tomllib
from pathlib import Path
from typing import Any

AGENT_ROOT = Path(__file__).resolve().parents[2]
GARDEN = AGENT_ROOT / "src" / "garden"
ROOT = AGENT_ROOT.parent
OPERATION_ID = re.compile(r"\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*\b")
ARCHITECTURE_DOC = "[architecture guardrails](docs/specs/architecture-guardrails.md)"


def _source_location(path: Path, line: int = 1) -> str:
    return f"{path.relative_to(ROOT)}:{line}"


def _subpackages() -> tuple[str, ...]:
    return tuple(
        sorted(
            path.name
            for path in GARDEN.iterdir()
            if path.is_dir() and path.name != "__pycache__" and any(path.rglob("*.py"))
        )
    )


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


def _violations(path: Path) -> list[str]:
    own = path.relative_to(GARDEN).parts[0]
    subpackages = set(_subpackages())
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve_import_module(path, node)
            if resolved is None:
                continue
            imported = [resolved]
            if resolved.count(".") == 1:
                imported.extend(
                    f"{resolved}.{alias.name}"
                    for alias in node.names
                    if (GARDEN / resolved.split(".")[1] / f"{alias.name}.py").is_file()
                    or (GARDEN / resolved.split(".")[1] / alias.name / "__init__.py").is_file()
                )
        else:
            continue
        for name in imported:
            parts = name.split(".")
            if len(parts) < 2 or parts[0] != "garden" or parts[1] not in subpackages:
                continue
            if parts[1] != own and len(parts) > 2:
                violations.append(f"{path}:{node.lineno} imports {name}")
    return violations


def test_layer_contract_covers_every_subpackage() -> None:
    config = tomllib.loads((AGENT_ROOT / "pyproject.toml").read_text())
    contracts = config["tool"]["importlinter"]["contracts"]
    layer_contract = next(
        item for item in contracts if item["name"] == "Agent subpackages follow strict layers"
    )
    configured = {
        name
        for layer in layer_contract["layers"]
        for value in layer.split(":")
        if (name := value.strip().removeprefix("garden."))
    }
    missing = sorted(set(_subpackages()) - configured)
    extra = sorted(configured - set(_subpackages()))
    violations = [
        f"{_source_location(next((GARDEN / name).rglob('*.py')))}: "
        f"{name} is missing from the layer contract"
        for name in missing
    ]
    violations.extend(f"agent/pyproject.toml:1: unknown layer package {name}" for name in extra)
    assert not violations, (
        "agent-layer-exhaustiveness: "
        + "; ".join(violations)
        + ". Add every discovered package to the layer contract; see "
        + ARCHITECTURE_DOC
    )


def test_subpackages_define_all() -> None:
    missing = [
        f"{_source_location(GARDEN / subpkg / '__init__.py')}: "
        "public package interface does not define __all__"
        for subpkg in _subpackages()
        if not _defines_all(GARDEN / subpkg / "__init__.py")
    ]
    assert not missing, (
        "agent-public-interface: "
        + "; ".join(missing)
        + ". Define __all__ and re-export supported names at the package root; see "
        + ARCHITECTURE_DOC
    )


def test_no_subpackage_imports_another_subpackages_internals() -> None:
    violations: list[str] = []
    for subpkg in _subpackages():
        for path in sorted((GARDEN / subpkg).rglob("*.py")):
            violations.extend(_violations(path))
    violations = [violation.replace(str(ROOT) + "/", "") for violation in violations]
    assert not violations, (
        "agent-public-interface: "
        + "; ".join(violations)
        + ". Import only from the other subpackage's public root; see "
        + ARCHITECTURE_DOC
    )


def test_skill_operation_ids_exist_in_published_contract() -> None:
    contract: dict[str, Any] = json.loads((ROOT / "contracts" / "openapi.json").read_text())
    operation_ids: set[str] = {
        operation["operationId"]
        for path in contract["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    permissions: set[str] = {
        operation["x-permission"]
        for path in contract["paths"].values()
        for operation in path.values()
        if isinstance(operation, dict) and "x-permission" in operation
    }
    skill_locations = {
        operation_id: (path, text.count("\n", 0, match.start()) + 1)
        for path in (GARDEN / "skills").rglob("SKILL.md")
        for text in [path.read_text()]
        for match in OPERATION_ID.finditer(text)
        for operation_id in [match.group()]
    }
    missing = sorted(set(skill_locations) - permissions - operation_ids)
    violations = [
        f"{_source_location(*skill_locations[operation_id])}: "
        f"{operation_id} is not a published operationId"
        for operation_id in missing
    ]
    assert not violations, (
        "agent-skill-operation-contract: "
        + "; ".join(violations)
        + ". Use an operationId from contracts/openapi.json; see "
        + ARCHITECTURE_DOC
    )
