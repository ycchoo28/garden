"""Each platform's recipe set must be closed: a recipe may depend only on recipes that exist there.

`just` rejects the whole justfile on a platform when a recipe depends on a recipe that is tagged
[unix] only (or [windows] only). That error shows up on Windows, so this test checks both sets on
any OS.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

HEADER = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*)[^:=]*:(?!=)(.*)$")
PLATFORMS = ("unix", "windows")


def parse_recipes(text: str) -> list[tuple[str, str | None, list[str]]]:
    """Return (name, platform tag, dependencies) for each recipe, in file order."""
    recipes = []
    tag: str | None = None
    for line in text.split("\n"):
        if line.startswith("[") and line.endswith("]"):
            tag = line[1:-1]
            continue
        match = HEADER.match(line)
        if match is None:
            continue
        name, rest = match.group(1), match.group(2)
        # Drop dependency arguments in parentheses and the `&&` separators, then keep the names.
        rest = re.sub(r"\([^)]*\)", " ", rest)
        deps = [word for word in rest.split() if word != "&&"]
        recipes.append((name, tag, deps))
        tag = None
    return recipes


def missing_dependencies(text: str, platform: str) -> list[str]:
    """Describe each dependency that is not a recipe on this platform."""
    recipes = parse_recipes(text)
    present = {name for name, tag, _ in recipes if tag != _other(platform)}
    problems = []
    for name, tag, deps in recipes:
        if tag == _other(platform):
            continue
        for dep in deps:
            if dep not in present:
                problems.append(
                    f"{platform}: {name} depends on {dep}, which is not a {platform} recipe"
                )
    return problems


def _other(platform: str) -> str:
    return "windows" if platform == "unix" else "unix"


def test_each_platform_recipe_set_has_only_its_own_dependencies() -> None:
    text = (ROOT / "justfile").read_text(encoding="utf-8")
    for platform in PLATFORMS:
        assert missing_dependencies(text, platform) == [], platform


def test_checker_flags_a_dependency_on_a_unix_only_recipe() -> None:
    # The bug that broke Windows: agent-e2e depended on up, which is [unix] only.
    text = "[unix]\nup:\n    echo up\n\nagent-e2e: up\n    echo e2e\n"

    assert missing_dependencies(text, "windows") == [
        "windows: agent-e2e depends on up, which is not a windows recipe"
    ]
    assert missing_dependencies(text, "unix") == []


def test_windows_demo_recipes_are_in_the_windows_set() -> None:
    text = (ROOT / "justfile").read_text(encoding="utf-8")
    windows = {name for name, tag, _ in parse_recipes(text) if tag != "unix"}

    assert {"setup", "demo", "demo-web", "demo-clean", "demo-uninstall"} <= windows
