"""Roles each module declares in code, and the permissions they grant."""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Role:
    name: str
    permissions: frozenset[str]


@dataclass(frozen=True)
class Permission:
    """A permission a module declares, and whether an agent may hold it.

    `agent_allowed` has no default, so every permission states it. False marks a person-only act,
    which a CLI login never holds.
    """

    name: str
    agent_allowed: bool


class UndeclaredPermissionError(RuntimeError):
    """A role grants a permission that no module declares. The app refuses to start."""


def check_roles_declare_permissions(roles: Iterable[Role], declared: Iterable[Permission]) -> None:
    """Refuse to start when a role names a permission that is not declared, so a typo is caught."""
    known = {permission.name for permission in declared}
    for role in roles:
        unknown = sorted(role.permissions - known)
        if unknown:
            raise UndeclaredPermissionError(
                f"Role {role.name} grants undeclared permission(s): {', '.join(unknown)}."
            )
