#!/usr/bin/env python3
"""Compare generated API artifacts without writing to the working tree."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
CONTRACT = ROOT / "contracts" / "openapi.json"
GENERATED = ROOT / "frontend" / "src" / "api" / "generated"


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="garden-api-check-") as temporary:
        temp = Path(temporary)
        fresh_contract = temp / "openapi.json"
        fresh_client = temp / "generated"
        subprocess.run(
            ["uv", "run", "python", "-m", "app.export_openapi", str(fresh_contract)],
            cwd=BACKEND,
            check=True,
        )
        subprocess.run(
            [
                "bunx",
                "openapi-ts",
                "--input",
                str(fresh_contract),
                "--output",
                str(fresh_client),
                "--plugins",
                "@hey-api/client-fetch",
                "@tanstack/vue-query",
                "zod",
                "--no-log-file",
            ],
            cwd=FRONTEND,
            check=True,
        )

        stale: list[str] = []
        if not CONTRACT.is_file() or CONTRACT.read_bytes() != fresh_contract.read_bytes():
            stale.append("contracts/openapi.json")
        tracked_client = _files(GENERATED)
        fresh_files = _files(fresh_client)
        stale.extend(
            f"frontend/src/api/generated/{name}"
            for name in sorted(set(tracked_client) | set(fresh_files))
            if tracked_client.get(name) != fresh_files.get(name)
        )
        if not stale:
            print("The OpenAPI contract and generated client are current.")
            return 0

        print("The generated API artifacts are stale:", file=sys.stderr)
        for name in stale:
            print(f"  {name}", file=sys.stderr)
        print("Run `just api` to regenerate them, then review the diff.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
