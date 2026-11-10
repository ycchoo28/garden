"""Check frontend page placement and frontend/backend package names."""

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = "docs/specs/architecture-guardrails.md"
APP_PAGES = {"HomePage.vue", "SignedOutPage.vue"}


def check(root: Path) -> list[str]:
    frontend = root / "frontend/src"
    failures: list[str] = []

    def fail(path: Path, rule: str, detail: str, alternative: str, line: int = 1) -> None:
        failures.append(
            f"{path.relative_to(root)}:{line}: {rule}: {detail}; {alternative}. See {DOC}"
        )

    if (frontend / "pages").exists():
        fail(
            frontend / "pages",
            "frontend-page-placement",
            "global pages root exists",
            "Place each page in its owning module, Core package or app",
        )
    route_sources = []
    for path in frontend.rglob("*"):
        if not path.is_file() or path.suffix not in {".ts", ".vue"}:
            continue
        text = path.read_text()
        if path.name in {"routes.ts", "router.ts"} or re.search(
            r"\b(?:createRouter|RouteRecordRaw|RouterView)\b", text
        ):
            route_sources.append((path, text))
    for route_path, route_text in route_sources:
        route_parts = route_path.relative_to(frontend).parts
        if not route_parts or route_parts[0] not in {"modules", "core", "app"}:
            fail(
                route_path,
                "frontend-page-placement",
                "route definition outside a business module, Core package or application layer",
                "Put route definitions beside their owning pages",
            )
        imports = re.finditer(
            r"(?:from\s*|import\s*\(?\s*)['\"]([^'\"]+\.vue)['\"]",
            route_text,
        )
        for match in imports:
            specifier = match.group(1)
            component = (
                frontend / specifier.removeprefix("@/")
                if specifier.startswith("@/")
                else route_path.parent / specifier
            ).resolve()
            try:
                component_parts = component.relative_to(frontend.resolve()).parts
            except ValueError:
                component_parts = ()
            allowed = (len(component_parts) >= 3 and component_parts[0] in {"modules", "core"}) or (
                len(component_parts) >= 2
                and component_parts[0] == "app"
                and component.name in APP_PAGES
            )
            if not allowed:
                line = route_text.count("\n", 0, match.start()) + 1
                fail(
                    route_path,
                    "frontend-page-placement",
                    f"route imports Vue component outside an allowed package ({match.group(1)})",
                    "Keep routed components in their owning package; app pages "
                    "must be explicitly listed",
                    line,
                )

    groups = (("modules", root / "backend/app/modules"), ("core", root / "backend/app/core"))
    for group, backend_parent in groups:
        front_parent = frontend / group
        backend_names = (
            {p.name for p in backend_parent.iterdir() if p.is_dir() and not p.name.startswith("__")}
            if backend_parent.exists()
            else set()
        )
        if front_parent.exists():
            for path in sorted(p for p in front_parent.iterdir() if p.is_dir()):
                if path.name not in backend_names:
                    fail(
                        path,
                        "frontend-backend-package-name",
                        f"{group} package {path.name!r} has no backend package of the same group",
                        "Use the matching backend package name or remove the frontend package",
                    )

    for path in sorted(
        p for p in frontend.rglob("*") if p.is_file() and p.suffix in {".ts", ".tsx", ".vue", ".js"}
    ):
        rel = path.relative_to(frontend)
        if rel.parts[:2] == ("api", "generated"):
            continue
        text = path.read_text()
        source_package = None
        if len(rel.parts) >= 3 and rel.parts[0] in {"core", "modules"}:
            source_package = (rel.parts[0], rel.parts[1])
        for specifier in re.findall(r"(?:from\s*|import\s*\()\s*['\"]([^'\"]+)['\"]", text):
            target = re.search(r"(?:^|/)(modules|core)/([^/]+)(.*)$", specifier)
            if not target:
                continue
            group, name, tail = target.groups()
            own = source_package == (group, name)
            outside = rel.parts[0] not in {"modules", "core"}
            if outside and tail not in {"", "/index", "/index.ts"}:
                line = next(
                    (i for i, value in enumerate(text.splitlines(), 1) if specifier in value), 1
                )
                fail(
                    path,
                    "frontend-entry-point",
                    f"import of {group}/{name} reaches a private path ({specifier})",
                    "Import only the package public entry point",
                    line,
                )
            if not outside and not own and group == "modules":
                line = next(
                    (i for i, value in enumerate(text.splitlines(), 1) if specifier in value), 1
                )
                fail(
                    path,
                    "frontend-module-boundary",
                    f"import of sibling package {group}/{name}",
                    "Use the generated client and Shared; sibling dependencies need an "
                    "explicit public boundary",
                    line,
                )
        network = re.search(
            r"\bfetch\s*\(|\baxios\s*\.|\bXMLHttpRequest\b|\bWebSocket\s*\(", text
        ) or re.search(r"['\"](?:https?://|/api/)", text)
        if network:
            line = text.count("\n", 0, network.start()) + 1
            fail(
                path,
                "frontend-server-call",
                "hand-written server URL or direct network call",
                "Use the generated API client",
                line,
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    failures = check(args.root.resolve())
    print("\n".join(failures) if failures else "Frontend architecture checks passed.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
