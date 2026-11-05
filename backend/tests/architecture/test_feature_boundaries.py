"""A feature's service imports only the ORM classes that its own repository imports.

A feature works with its own tables through its repository, so the names the repository imports are
the tables the feature owns or reads. A service that imports any other model class is reaching into
another feature's table around that feature's service. Services take and return plain views instead.
"""

import ast
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"


def _model_names(path: Path) -> set[str]:
    """Names imported from a business module's models file, such as app.modules.claims.models."""
    names: set[str] = set()
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level != 0 or node.module is None:
            continue
        parts = node.module.split(".")
        if len(parts) == 4 and parts[:2] == ["app", "modules"] and parts[3] == "models":
            names.update(alias.name for alias in node.names)
    return names


def _feature_folders() -> list[Path]:
    """Feature folders sit one level below a module: app/modules/<module>/<feature>/."""
    return sorted(path.parent for path in APP.glob("modules/*/*/service.py"))


def test_feature_services_import_only_the_models_their_repository_imports() -> None:
    features = _feature_folders()
    assert features, "no feature folders found, so the test would check nothing"
    violations: list[str] = []
    for feature in features:
        repository = feature / "repository.py"
        owned = _model_names(repository) if repository.is_file() else set()
        foreign = sorted(_model_names(feature / "service.py") - owned)
        if foreign:
            violations.append(f"{feature.relative_to(APP)}/service.py imports {foreign}")
    assert violations == []
