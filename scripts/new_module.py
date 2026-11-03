"""Scaffold a business module: backend package, schema migration, starter tests and frontend folder.

Run from the repository root with `just new-module <name>`. The file set lives in
scripts/templates/module (files end in .tmpl so the backend tools never scan them). This script
fills in the module name, registers the module in backend/app/composition.py, and regenerates the
API contract and client so the new module is in them.
"""

import re
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
TEMPLATES = ROOT / "scripts" / "templates" / "module"
COMPOSITION = BACKEND / "app" / "composition.py"
IMPORTS_ANCHOR = "# new-module: insert imports above this line"
ENTRIES_ANCHOR = "# new-module: insert entries above this line"
NAME_PATTERN = re.compile(r"[a-z][a-z0-9_]{1,30}")
CORE_NAMES = frozenset({"users", "audit", "files", "confirmations", "core", "shared", "app"})


def fill(text: str, values: dict[str, str]) -> str:
    for placeholder, value in values.items():
        text = text.replace(placeholder, value)
    return text


def register(name: str) -> None:
    text = COMPOSITION.read_text()
    if IMPORTS_ANCHOR not in text or ENTRIES_ANCHOR not in text:
        raise SystemExit(f"{COMPOSITION} is missing the new-module anchors")
    imports = (
        f"from app.modules import {name}\n"
        f"from app.modules.{name}.models import Base as {name}_base\n"
    )
    entry = (
        f'    InstalledModule("{name}", {name}.router, {name}.ROLES, {name}.PERMISSIONS, '
        f"{name}_base.metadata),\n"
    )
    text = text.replace(f"{IMPORTS_ANCHOR}\n", f"{imports}{IMPORTS_ANCHOR}\n", 1)
    text = text.replace(f"{ENTRIES_ANCHOR}\n", f"{entry}{ENTRIES_ANCHOR}\n", 1)
    COMPOSITION.write_text(text)


def run(command: list[str], cwd: Path) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not NAME_PATTERN.fullmatch(argv[1]):
        print(
            "usage: new_module.py <name>  (lowercase letters, digits and underscores, "
            "starting with a letter, 2 to 31 characters)",
            file=sys.stderr,
        )
        return 2
    name = argv[1]
    if name in CORE_NAMES:
        print(f"{name} is the name of a core module", file=sys.stderr)
        return 2

    head, *rest = name.split("_")
    values = {
        "__NAME__": name,
        "__CLASS__": "".join(part.capitalize() for part in [head, *rest]),
        "__CAMEL__": head + "".join(part.capitalize() for part in rest),
        "__REVISION__": secrets.token_hex(6),
    }
    plan: list[tuple[Path, str]] = []
    for template in sorted(TEMPLATES.rglob("*.tmpl")):
        relative = fill(template.relative_to(TEMPLATES).as_posix(), values).removesuffix(".tmpl")
        plan.append((ROOT / relative, fill(template.read_text(), values)))

    existing = [target for target, _ in plan if target.exists()]
    if existing or (BACKEND / "app" / "modules" / name).exists():
        print(f"{name} already exists; remove it first or pick another name", file=sys.stderr)
        return 2

    for target, content in plan:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    register(name)

    python_files = [
        str(path.relative_to(BACKEND))
        for path, _ in plan
        if path.is_relative_to(BACKEND) and path.suffix == ".py"
    ]
    python_files.append(str(COMPOSITION.relative_to(BACKEND)))
    tidy = ["uv", "run", "ruff", "format", *python_files]
    run(tidy, cwd=BACKEND)
    run(["uv", "run", "ruff", "check", "--fix", *python_files], cwd=BACKEND)
    run(tidy, cwd=BACKEND)
    run(
        ["uv", "run", "python", "-m", "app.export_openapi", "../contracts/openapi.json"],
        cwd=BACKEND,
    )
    run(["bun", "run", "generate"], cwd=FRONTEND)

    for target, _ in plan:
        print(f"created {target.relative_to(ROOT)}")
    print(f"registered {name} in {COMPOSITION.relative_to(ROOT)} and regenerated the API client")
    print("next: fill in the module, then run just check")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
