"""Check backend and frontend Shared packages for neutrality."""

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/specs/architecture-guardrails.md"
SCHEMA_TABLE_SQL = re.compile(
    r"\b(?:from|join|into|update|table|references)\s+"
    r"[\"`]?([a-z_][\w$]*)[\"`]?\s*\.\s*[\"`]?([a-z_][\w$]*)[\"`]?",
    re.I,
)
WORD_EXCEPTIONS = {
    "month": "Generic Year-month helper is intentionally shared.",
    "months": "Generic Year-month helper is intentionally shared.",
    "year": "Generic calendar value used with the Year-month helper.",
    "collections": "Python standard-library collection protocols are generic plumbing.",
    "collection": "Generic collection protocol in shared typing.",
    "search": "URL query strings are generic browser navigation data.",
    "and": "Common conjunction in comments and prose.",
    "import": "Python import statements are language syntax.",
    "document": "Generic RFC problem document type.",
    "result": "Generic result value in shared helpers.",
    "mode": "Generic display mode in theme helpers.",
    "line": "Generic line number in diagnostics.",
    "close": "Generic resource lifecycle operation.",
    "rule": "Generic validation rule in shared errors.",
    "queue": "Generic queue helper.",
}


def words_in(text: str) -> set[str]:
    parts = re.findall(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[A-Z]+|\d+", text)
    words: set[str] = set()
    for part in parts:
        word = part.lower()
        words.add(word)
        if word.endswith("ies") and len(word) > 4:
            words.add(word[:-3] + "y")
        elif (
            word.endswith("es")
            and word[:-2].endswith(("ch", "sh", "ss", "x", "z"))
            and len(word) > 4
        ):
            words.add(word[:-2])
        elif word.endswith("s") and not word.endswith(("ss", "us")) and len(word) > 3:
            words.add(word[:-1])
    return words


def business_words(root: Path) -> set[str]:
    words: set[str] = set()
    for parent in (root / "backend/app/modules", root / "frontend/src/modules"):
        if not parent.is_dir():
            continue
        for module in parent.iterdir():
            if module.is_dir():
                words.update(words_in(module.name))
                for feature in module.iterdir():
                    if (
                        feature.is_dir()
                        and not feature.name.startswith(("__", "."))
                        and feature.name not in {"tests", "__pycache__"}
                    ):
                        words.update(words_in(feature.name))
    glossary = root / "GLOSSARY.md"
    if glossary.exists():
        content = glossary.read_text()
        sections = re.split(r"(?m)^## ", content)
        for section in sections:
            heading = section.splitlines()[0].strip().lower() if section else ""
            if words_in(heading) & words:
                words.update(words_in(" ".join(re.findall(r"\*\*(.*?)\*\*", section))))
    for exception in WORD_EXCEPTIONS:
        words.discard(exception)
    return words


def specific_access_values(root: Path) -> set[str]:
    values: set[str] = set()
    for path in (root / "backend/app/modules", root / "backend/app/core"):
        for permissions in path.rglob("permissions.py") if path.exists() else ():
            tree = ast.parse(permissions.read_text(), filename=str(permissions))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    values.add(node.value)
    return values


def check(root: Path) -> tuple[list[str], list[str]]:
    words = business_words(root)
    access_values = specific_access_values(root)
    failures: list[str] = []

    def fail(path: Path, rule: str, detail: str, alternative: str, line: int = 1) -> None:
        failures.append(
            f"{path.relative_to(root)}:{line}: {rule}: {detail}; {alternative}. See {DOC}"
        )

    def line_of(path: Path, predicate) -> int:
        return next(
            (
                index
                for index, value in enumerate(path.read_text().splitlines(), 1)
                if predicate(value)
            ),
            1,
        )

    # Parse all imports, including imports inside functions, with Python's AST.
    backend = root / "backend/app/shared"
    for path in sorted(backend.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        local_strings = {
            node.targets[0].id: node.value.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            and node.targets
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        }
        for node in ast.walk(tree):
            targets = (
                [node.module]
                if isinstance(node, ast.ImportFrom)
                else ([alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
            )
            for target in targets:
                if isinstance(node, ast.ImportFrom) and node.level:
                    package = "app.shared"
                    depth = len(package.split(".")) - node.level + 1
                    target = ".".join(package.split(".")[:depth] + ([target] if target else []))
                if target and (target.startswith("app.core") or target.startswith("app.modules")):
                    fail(
                        path,
                        "shared-imports",
                        target,
                        "Move the dependency to its owning package or a higher composition layer",
                        node.lineno,
                    )
        text = path.read_text()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                constructor = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else ""
                )
                first = node.args[0] if node.args else None
                value = (
                    first.value
                    if isinstance(first, ast.Constant) and isinstance(first.value, str)
                    else local_strings.get(first.id)
                    if isinstance(first, ast.Name)
                    else None
                )
                if constructor in {"Permission", "Role"} and value is not None:
                    fail(
                        path,
                        "shared-kind",
                        f"{constructor} declaration with specific value {value!r}",
                        "Declare permission and role values in their owning package",
                        node.lineno,
                    )
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in access_values:
                fail(
                    path,
                    "shared-kind",
                    f"specific permission or role value {node.value!r}",
                    "Declare permission and role values in their owning package",
                    node.lineno,
                )
                break
        if re.search(r"\b(?:APIRouter|Router)\b|\b(?:Table|mapped_column)\s*\(", text):
            fail(
                path,
                "shared-kind",
                "router or database table definition",
                "Keep routes and table definitions in their owning package",
                line_of(
                    path,
                    lambda value: bool(
                        re.search(r"\b(?:APIRouter|Router)\b|\b(?:Table|mapped_column)\s*\(", value)
                    ),
                ),
            )
        schema_sql = next(
            (
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and SCHEMA_TABLE_SQL.search(node.value)
            ),
            None,
        )
        if schema_sql:
            match = SCHEMA_TABLE_SQL.search(schema_sql.value)
            fail(
                path,
                "shared-kind",
                f"SQL names schema {match.group(1)!r}",
                "Keep schema-specific SQL in an owning repository",
                schema_sql.lineno,
            )
        for word in sorted(words):
            if word in words_in(path.name + "\n" + text):
                fail(
                    path,
                    "shared-vocabulary",
                    word,
                    "Use domain-neutral names and examples",
                    line_of(path, lambda value, target=word: target in words_in(value)),
                )
                break

    frontend = root / "frontend/src/shared"
    for path in sorted(
        p for p in frontend.rglob("*") if p.is_file() and p.suffix in {".ts", ".vue", ".js"}
    ):
        text = path.read_text()
        if re.search(
            r"(?:from\s+|import\s*\()['\"](?:\.\.?/)*api(?:/|['\"])|\bfetch\s*\(|\baxios\.", text
        ):
            fail(
                path,
                "shared-imports",
                "generated client or server call",
                "Keep server calls in the generated client and owning module",
                line_of(
                    path,
                    lambda value: bool(
                        re.search(r"\bfetch\s*\(|\baxios\.", value)
                        or re.search(r"(?:from\s+|import\s*\()[^\n]*api/", value)
                    ),
                ),
            )
        if re.search(
            r"from\s+['\"](?:@/|\.\.?/)*(?:modules|core)/|import\s*\(['\"](?:@/|\.\.?/)*(?:modules|core)/",
            text,
        ):
            fail(
                path,
                "shared-imports",
                "Core or business package",
                "Keep Shared below the application and package layers",
                line_of(
                    path,
                    lambda value: bool(
                        re.search(r"(?:from\s+|import\s*\()[^\n]*(?:modules|core)/", value)
                    ),
                ),
            )
        if path.name.endswith("Page.vue") or re.search(
            r"createRouter|RouteRecordRaw|(?:^|\s)path\s*:", text
        ):
            fail(
                path,
                "shared-kind",
                "routed page or route definition",
                "Place routed pages and routes in the application or owning package",
            )
        if re.search(
            r"(?:permission\s*[:=]\s*['\"]|permissions\.includes\([^)]*['\"])[a-z]+\.[a-z]",
            text,
            re.I,
        ):
            fail(
                path,
                "shared-kind",
                "specific permission string",
                "Keep permission values in their owning package",
            )

        for word in sorted(words):
            if word in words_in(path.name + "\n" + text):
                fail(
                    path,
                    "shared-vocabulary",
                    word,
                    "Use domain-neutral names and examples",
                    line_of(path, lambda value, target=word: target in words_in(value)),
                )
                break

    # Informational only: Shared helpers with a single business-module importer.
    info: list[str] = []
    try:
        sys.path.insert(0, str(root / "backend"))
        import grimp

        graph = grimp.build_graph("app", include_external_packages=False, cache_dir=None)
        for module in sorted(m for m in graph.modules if m.startswith("app.shared.")):
            importers = {
                m
                for m in graph.find_modules_that_directly_import(module)
                if m.startswith("app.modules.")
            }
            consuming_modules = {".".join(m.split(".")[:3]) for m in importers}
            if len(consuming_modules) == 1:
                info.append(
                    "INFO shared-single-consumer: "
                    f"{module} is used only by {next(iter(consuming_modules))}"
                )
    except ImportError:
        info.append(
            "INFO shared-single-consumer: unavailable (install backend architecture dependencies)"
        )

    shared_consumers: dict[Path, set[str]] = {}
    frontend_root = root / "frontend/src"
    for path in frontend_root.rglob("*") if frontend_root.exists() else ():
        if (
            not path.is_file()
            or path.suffix not in {".ts", ".vue", ".js"}
            or "shared" in path.parts
        ):
            continue
        parts = path.relative_to(frontend_root).parts
        if len(parts) < 3 or parts[0] not in {"modules", "core"}:
            continue
        owner = f"{parts[0]}/{parts[1]}"
        for specifier in re.findall(
            r"(?:from\s*|import\s*\()\s*['\"]([^'\"]+)['\"]", path.read_text()
        ):
            if (
                "/shared/" in specifier
                or specifier.startswith("@/shared/")
                or specifier.startswith("../shared/")
            ):
                target = (
                    frontend_root / specifier.removeprefix("@/")
                    if specifier.startswith("@/")
                    else (path.parent / specifier)
                ).resolve()
                candidates = [
                    target,
                    target.with_suffix(".ts"),
                    target.with_suffix(".vue"),
                    target.with_suffix(".js"),
                    target / "index.ts",
                ]
                target = next(
                    (candidate for candidate in candidates if candidate.is_file()), target
                )
                shared_target = frontend_root / "shared"
                if target.is_relative_to(shared_target):
                    shared_consumers.setdefault(target, set()).add(owner)
    for target, consumers in sorted(shared_consumers.items()):
        if len(consumers) == 1:
            info.append(
                "INFO shared-single-consumer: frontend "
                f"{target.relative_to(frontend_root)} is used only by {next(iter(consumers))}"
            )

    return failures, info


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    failures, info = check(args.root.resolve())
    print(
        "\n".join(info + failures)
        if failures
        else "\n".join([*info, "Shared architecture checks passed."])
    )
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
