"""The rules for putting a prepared batch live. Pure: no database and no clock.

A document is one uploaded file, named within its collection. A batch replaces the live document
with the same name. A full set also removes the live documents it does not name.
"""

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# Removing this many documents or more in one batch is flagged for attention.
LARGE_REMOVAL_DOCUMENTS = 3

LARGE_REMOVAL = "large_removal"
ACCESS_RULES_CHANGED = "access_rules_changed"


def staged_digest(payload: Mapping[str, Any]) -> str:
    """SHA-256 of the staged result in canonical JSON form. Any change changes the digest."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def documents_to_close(
    *, live: Iterable[str], staged: Iterable[str], full_set: bool
) -> tuple[set[str], set[str]]:
    """The live documents a batch replaces, and the ones it removes. Returns (replaced, removed)."""
    live_names = set(live)
    staged_names = set(staged)
    replaced = live_names & staged_names
    removed: set[str] = live_names - staged_names if full_set else set()
    return replaced, removed


def risk_triggers(*, removed: int, rules_changed: bool) -> list[str]:
    triggers: list[str] = []
    if removed >= LARGE_REMOVAL_DOCUMENTS:
        triggers.append(LARGE_REMOVAL)
    if rules_changed:
        triggers.append(ACCESS_RULES_CHANGED)
    return triggers


def normalise_rules(rules: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Rules in a fixed form, so two sets that grant the same reach compare equal."""
    return sorted(
        (
            {
                "role": str(rule["role"]),
                "all_departments": bool(rule["all_departments"]),
                "department_codes": sorted(str(code) for code in rule["department_codes"]),
            }
            for rule in rules
        ),
        key=lambda rule: str(rule["role"]),
    )


def same_rules(first: Sequence[Mapping[str, Any]], second: Sequence[Mapping[str, Any]]) -> bool:
    return normalise_rules(first) == normalise_rules(second)
