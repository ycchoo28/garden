"""Credential behavior through the auth interface, including Windows keychain setup."""

import importlib
import sys
from pathlib import Path

import keyring
import pytest

from garden.auth import load_session, logout, store_token
from garden.shared import Settings


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("GARDEN_SECRET_STORE", raising=False)
    monkeypatch.setattr(keyring, "get_keyring", lambda: object())

    def empty_password(_service: str, _account: str) -> None:
        return None

    monkeypatch.setattr(keyring, "get_password", empty_password)
    return Settings("http://portal.example", tmp_path, tmp_path / "dsh")


def test_windows_hides_cffi_so_pywin32_ctypes_uses_its_ctypes_backend(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "cffi", raising=False)

    assert load_session(settings) is None

    with pytest.raises(ImportError):
        importlib.import_module("cffi")


def test_linux_leaves_cffi_alone(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delitem(sys.modules, "cffi", raising=False)

    assert load_session(settings) is None

    assert "cffi" not in sys.modules


def test_default_store_hides_cffi_before_it_asks_keyring_for_a_backend(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "cffi", raising=False)
    cffi_hidden_when_asked: list[bool] = []

    def backend_for_this_system() -> object:
        try:
            importlib.import_module("cffi")
        except ImportError:
            cffi_hidden_when_asked.append(True)
        else:
            cffi_hidden_when_asked.append(False)
        return object()

    monkeypatch.setattr(keyring, "get_keyring", backend_for_this_system)

    assert load_session(settings) is None

    assert cffi_hidden_when_asked == [True]


def test_logout_clears_only_the_current_portals_keychain_entry(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    entries: dict[tuple[str, str], str] = {}

    def get(service: str, account: str) -> str | None:
        return entries.get((service, account))

    monkeypatch.setattr(keyring, "get_password", get)

    def save(service: str, account: str, value: str) -> None:
        entries[service, account] = value

    def delete(service: str, account: str) -> None:
        entries.pop((service, account), None)

    monkeypatch.setattr(keyring, "set_password", save)
    monkeypatch.setattr(keyring, "delete_password", delete)
    other = Settings("http://other.example", settings.home, settings.dsh_home)
    store_token("first-token", settings=settings)
    store_token("other-token", settings=other)

    previous, warning = logout(settings=settings)

    assert previous is not None and previous.token == "first-token"
    assert warning is None
    assert load_session(settings) is None
    remaining = load_session(other)
    assert remaining is not None and remaining.token == "other-token"
