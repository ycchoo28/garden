"""Build `garden.pyz`: the garden package with its locked runtime dependencies vendored in.

The zipapp needs only a Python 3.12 interpreter on the laptop, so no uv or pip is required there.
Usage: python scripts/build_zipapp.py [OUTPUT]. The default output is dist/garden.pyz.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import zipapp
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "dist" / "garden.pyz"
CONTRACT = ROOT.parent / "contracts" / "openapi.json"
WRITE_METHODS = ("post", "put", "patch", "delete")


def build(output: Path) -> Path:
    staging = ROOT / "dist" / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    app = staging / "app"
    app.mkdir(parents=True)
    requirements = subprocess.run(
        [
            "uv",
            "export",
            "--frozen",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--format",
            "requirements-txt",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    requirements_file = staging / "requirements.txt"
    requirements_file.write_text(requirements, encoding="utf-8")
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--target",
            str(app),
            "--requirement",
            str(requirements_file),
        ],
        cwd=ROOT,
        check=True,
    )
    # The first pass keeps only the packages for this build machine (Linux), so a Windows laptop
    # would lack pywin32-ctypes, which keyring needs for Windows Credential Manager. A second pass
    # for Windows adds those pure-Python packages to the same folder.
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python-platform",
            "x86_64-pc-windows-msvc",
            "--python-version",
            "3.12",
            "--target",
            str(app),
            "--requirement",
            str(requirements_file),
        ],
        cwd=ROOT,
        check=True,
    )
    shutil.copytree(
        ROOT / "src" / "garden", app / "garden", ignore=shutil.ignore_patterns("__pycache__")
    )
    # The entry script passes the exit code through; zipapp's own `main=` option would drop it.
    (app / "__main__.py").write_text(
        "import sys\n\nfrom garden.cli import main\n\nsys.exit(main())\n", encoding="utf-8"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    zipapp.create_archive(app, target=output, interpreter="/usr/bin/env python3", compressed=True)
    shutil.rmtree(staging, ignore_errors=True)
    return output


def saves_table(contract: Path) -> str:
    """The saves each module offers in the contract, and which are critical (`x-agent.confirm`).

    A reviewer reads this to spot a critical route that someone forgot to mark.
    """
    document = json.loads(contract.read_text(encoding="utf-8"))
    by_module: dict[str, list[str]] = defaultdict(list)
    for path, methods in document["paths"].items():
        for method, operation in methods.items():
            if method not in WRITE_METHODS:
                continue
            operation_id = operation["operationId"]
            critical = bool((operation.get("x-agent") or {}).get("confirm"))
            marker = "critical" if critical else "ordinary"
            module = operation_id.split(".", 1)[0]
            by_module[module].append(f"  {marker:<9} {method.upper():<6} {operation_id}  {path}")
    lines = ["Saves in the API contract, by module (critical ones need approval in an agent):"]
    for module in sorted(by_module):
        lines.append(f"{module}:")
        lines.extend(sorted(by_module[module], key=lambda row: row.split()[0] != "critical"))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    output = Path(argv[1]) if len(argv) > 1 else DEFAULT_OUTPUT
    print(build(output.resolve()))
    print(saves_table(CONTRACT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
