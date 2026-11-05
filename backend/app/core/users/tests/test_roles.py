"""Role grants from every module combine by role name, and an unknown role stops the app."""

import pytest

from app.composition import MODULES, InstalledModule
from app.main import create_app
from app.shared.permissions import Role, UnknownRoleError, merge_roles


def test_grants_to_the_same_role_combine_instead_of_overwriting() -> None:
    merged = merge_roles(
        [Role("finance", frozenset({"a.view"})), Role("finance", frozenset({"b.view"}))],
        ["finance"],
    )
    assert merged == (Role("finance", frozenset({"a.view", "b.view"})),)


def test_installed_modules_each_keep_their_grants_to_finance() -> None:
    finance = next(role for role in create_app().state.roles if role.name == "finance")
    assert {
        "audit.view",
        "claims.decide",
        "budgets.manage",
        "budgets.month.close",
    } <= finance.permissions


def test_admin_holds_every_permission_any_other_role_grants() -> None:
    roles = create_app().state.roles
    admin = next(role for role in roles if role.name == "admin")
    for role in roles:
        if role.name != "admin":
            assert role.permissions <= admin.permissions, role.name


def test_admin_holds_every_declared_permission() -> None:
    app = create_app()
    admin = next(role for role in app.state.roles if role.name == "admin")
    declared = {permission.name for permission in app.state.permissions}
    assert declared <= admin.permissions, declared - admin.permissions


def test_a_module_granting_to_an_unknown_role_stops_the_app() -> None:
    first = MODULES[0]
    typo = InstalledModule(
        "typo", first.router, (Role("finanse", frozenset()),), (), first.metadata
    )
    with pytest.raises(UnknownRoleError):
        _ = create_app((*MODULES, typo))
