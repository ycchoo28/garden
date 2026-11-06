"""Typed reads of JSON from the portal and from files.

A value of the wrong shape reads as empty, so a malformed document cannot crash the CLI or server.
The `is_*` guards narrow to fully typed containers, which keeps strict type checking clean.
"""

from __future__ import annotations

from typing import Any, TypeGuard


def is_object(value: object) -> TypeGuard[dict[str, Any]]:
    return isinstance(value, dict)


def is_list(value: object) -> TypeGuard[list[Any]]:
    return isinstance(value, list)


def as_object(value: object) -> dict[str, Any]:
    if is_object(value):
        return value
    return {}


def as_list(value: object) -> list[Any]:
    if is_list(value):
        return value
    return []


def as_text(value: object, default: str = "") -> str:
    return value if isinstance(value, str) else default
