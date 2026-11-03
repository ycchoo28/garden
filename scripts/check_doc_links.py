"""Check the relative links in every tracked Markdown file.

A link to a file must point at a file or folder in the repository, and a link with a #heading must
match a heading in the target file, using the anchor rules Forgejo uses. Web links are not fetched,
so the check needs no network. Links inside code blocks and inline code are ignored.

Run it with `just docs-check`.
"""

import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
HEADING = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")
INLINE_CODE = re.compile(r"`[^`]*`")


def _prose_lines(text: str) -> list[tuple[int, str]]:
    """The lines outside code blocks, numbered from 1."""
    lines: list[tuple[int, str]] = []
    in_fence = False
    for number, line in enumerate(text.splitlines(), start=1):
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append((number, line))
    return lines


def _slug(heading: str) -> str:
    """The anchor Forgejo gives a heading: lower case, punctuation dropped, spaces to dashes."""
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", heading)
    text = text.replace("`", "").lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    found: set[str] = set()
    seen: dict[str, int] = {}
    for _, line in _prose_lines(path.read_text(encoding="utf-8")):
        match = HEADING.match(line)
        if not match:
            continue
        slug = _slug(match.group(1))
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        found.add(slug if count == 0 else f"{slug}-{count}")
    return found


def _broken(source: Path) -> list[str]:
    problems: list[str] = []
    for number, line in _prose_lines(source.read_text(encoding="utf-8")):
        for target in LINK.findall(INLINE_CODE.sub("", line)):
            if re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE) or target.startswith("<"):
                continue
            file_part, _, anchor = target.partition("#")
            resolved = (source.parent / unquote(file_part)).resolve() if file_part else source
            where = f"{source.relative_to(ROOT)}:{number}"
            if not resolved.exists():
                problems.append(f"{where}: {target} points at a missing file")
            elif anchor and resolved.suffix == ".md" and anchor.lower() not in _anchors(resolved):
                problems.append(f"{where}: {target} points at a missing heading")
    return problems


def main() -> int:
    tracked = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.split()
    problems = [problem for name in tracked for problem in _broken(ROOT / name)]
    for problem in problems:
        print(problem)
    if problems:
        print(f"{len(problems)} broken link(s) in the Markdown files.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
