"""Enforce the authoring rule on tracked and unignored text files."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
        )
        .decode()
        .split("\0")
    )
    failures = []
    for name in filter(None, paths):
        path = ROOT / name
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), 1):
            if chr(0x2014) in line:
                failures.append(f"{name}:{number}: authoring-dash: use a plain dash. See AGENTS.md")
    print("\n".join(failures) if failures else "Authoring check passed.")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
