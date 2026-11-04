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


class UnknownRoleError(RuntimeError):
    """A module grants to a role the users core does not know. The app refuses to start."""


def merge_roles(roles: Iterable[Role], known_names: Iterable[str]) -> tuple[Role, ...]:
    """Combine the grants every module makes to the same role, so no module overwrites another.

    A role name the users core does not know is refused, so a typo cannot grant nothing silently.
    """
    known = frozenset(known_names)
    merged: dict[str, frozenset[str]] = {}
    for role in roles:
        if role.name not in known:
            raise UnknownRoleError(f"Role {role.name} is not a role the users core knows.")
        merged[role.name] = merged.get(role.name, frozenset()) | role.permissions
    return tuple(Role(name, permissions) for name, permissions in merged.items())


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
