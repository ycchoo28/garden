"""Fail when a deny-listed term appears in a tracked or new file.

Terms live base64-encoded in security/denylist.txt, so this repo does not spell them out. The scan
covers the files git would commit (tracked and untracked, minus ignored ones). The .scratch/ folder
holds working notes and is not scanned. The generated client is not scanned either: it is rebuilt
from contracts/openapi.json, which is scanned, and its comments come from the generator. Binary files
and this check's own files are skipped.
"""

import base64
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "security" / "denylist.txt"
SKIPPED_PREFIXES = (".scratch/", "frontend/src/api/generated/")
SKIPPED_FILES = {"scripts/deny_list_check.py", "security/denylist.txt"}


def load_patterns() -> list[tuple[str, re.Pattern[str]]]:
    patterns: list[tuple[str, re.Pattern[str]]] = []
    for number, line in enumerate(LIST.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        kind, _, encoded = line.partition(" ")
        if kind not in ("substring", "word") or not encoded:
            raise SystemExit(f"{LIST}:{number}: expected 'substring <base64>' or 'word <base64>'")
        term = base64.b64decode(encoded, validate=True).decode("utf-8").lower()
        body = re.escape(term) if kind == "substring" else rf"\b{re.escape(term)}\b"
        patterns.append((term, re.compile(body, re.IGNORECASE)))
    return patterns


def candidate_files() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout
    return [
        path
        for path in listed.decode("utf-8").split("\0")
        if path and path not in SKIPPED_FILES and not path.startswith(SKIPPED_PREFIXES)
    ]


def main() -> int:
    patterns = load_patterns()
    findings: list[str] = []
    files = candidate_files()
    for path in files:
        data = (ROOT / path).read_bytes()
        if b"\0" in data:
            continue
        for number, line in enumerate(data.decode("utf-8", errors="replace").splitlines(), start=1):
            for term, pattern in patterns:
                if pattern.search(line):
                    findings.append(f"{path}:{number}: deny-listed term '{term}'")
    if findings:
        print("\n".join(findings))
        print(f"deny-list check failed: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print(f"deny-list check passed: {len(files)} files scanned, {len(patterns)} terms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
