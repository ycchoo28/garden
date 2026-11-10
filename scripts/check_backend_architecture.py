"""Check discovered package boundaries using the same import graph as Import Linter."""

import sys
from pathlib import Path

import grimp

ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/adr/0026-business-ownership-and-reporting-reads.md"
# Worker session lifetime surrounds several repositories and services.
DB_EXCEPTIONS = {
    "app.modules.knowledge.batches.jobs": "Worker composition owns one session per job.",
    "app.shared.jobs": "Postgres job queue connection plumbing.",
    "app.seed": "Development composition creates fixtures across modules.",
    "app.demo_seed": "Development composition creates demo records across modules.",
    "app.migrate": "Migration entry point creates database infrastructure.",
}


def main() -> int:
    sys.path.insert(0, str(ROOT / "backend"))
    graph = grimp.build_graph("app", include_external_packages=True, cache_dir=None)
    modules = graph.modules
    packages = {
        m for m in modules if len(m.split(".")) == 3 and m.startswith(("app.core.", "app.modules."))
    }
    features = {
        m.rsplit(".", 1)[0] for m in modules if m.endswith(".repository") and len(m.split(".")) > 4
    }
    edges: dict[str, set[str]] = {m: set() for m in packages if m.startswith("app.modules.")}
    edge_sources: dict[tuple[str, str], tuple[str, str]] = {}
    failures: list[str] = []

    def fail(rule: str, source: str, target: str, alternative: str) -> None:
        details = graph.get_import_details(importer=source, imported=target)
        line = details[0]["line_number"] if details else 1
        relative = Path(*source.split("."))
        file = ROOT / "backend" / relative.with_suffix(".py")
        if not file.exists():
            file = ROOT / "backend" / relative / "__init__.py"
        failures.append(
            f"{file.relative_to(ROOT)}:{line}: {rule}: {target}; {alternative}. See {DOC}"
        )

    for source in sorted(modules):
        if not source.startswith("app.") or ".tests" in source:
            continue
        own = next((p for p in packages if source == p or source.startswith(p + ".")), None)
        for target in sorted(graph.find_modules_directly_imported_by(source)):
            foreign = next((p for p in packages if target == p or target.startswith(p + ".")), None)
            if own and foreign and own != foreign and target != foreign:
                fail(
                    "module-interface",
                    source,
                    target,
                    "Import the owning package interface",
                )
            for feature in features:
                if (
                    target == feature + ".repository"
                    and own
                    and source != feature
                    and not source.startswith(feature + ".")
                ):
                    fail(
                        "feature-repository",
                        source,
                        target,
                        "Use the feature service or public interface",
                    )
            if own in edges and foreign in edges and own != foreign:
                edges[own].add(foreign)
                edge_sources[own, foreign] = (source, target)
            database = (
                target.split(".", 1)[0] in {"sqlalchemy", "psycopg", "psycopg_pool", "asyncpg"}
                or target == "app.shared.db"
            )
            allowed = (
                source.endswith((".repository", ".models"))
                or source == "app.shared.db"
                or source in DB_EXCEPTIONS
                or source == "app.composition"
            )
            if database and not allowed:
                fail(
                    "database-layer",
                    source,
                    target,
                    "Put database access in a repository or model",
                )

    def visit(node: str, trail: tuple[str, ...]) -> None:
        if node in trail:
            source, target = edge_sources[trail[-1], node]
            fail(
                "module-cycle",
                source,
                target,
                "Invert the dependency through a port: " + " -> ".join((*trail, node)),
            )
            return
        for child in sorted(edges[node]):
            visit(child, (*trail, node))

    for node in edges:
        visit(node, ())
    print("\n".join(failures) if failures else "Backend architecture checks passed.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
