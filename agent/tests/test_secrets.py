"""Credential behavior through the auth interface, including Windows keychain setup."""

import importlib
import sys
from pathlib import Path

import keyring
import pytest
from keyring.errors import KeyringError

from garden.auth import load_session, logout, store_token
from garden.auth.secrets import KeyringStore
from garden.shared import GardenError, Settings


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


def test_logout_keeps_token_when_remote_revoke_fails(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    entries: dict[tuple[str, str], str] = {}

    def get(service: str, account: str) -> str | None:
        return entries.get((service, account))

    def save(service: str, account: str, value: str) -> None:
        entries[service, account] = value

    def delete(service: str, account: str) -> None:
        entries.pop((service, account), None)

    monkeypatch.setattr(keyring, "get_password", get)
    monkeypatch.setattr(keyring, "set_password", save)
    monkeypatch.setattr(keyring, "delete_password", delete)

    store_token("active-token", settings=settings)

    def failing_revoke(_token: str) -> None:
        from garden.shared.errors import GardenError

        raise GardenError(title="Unreachable portal")

    previous, warning = logout(settings=settings, revoke_remote=failing_revoke)
    assert previous is not None and previous.token == "active-token"
    assert warning == "Unreachable portal"
    active = load_session(settings)
    assert active is not None
    assert active.token == "active-token"

    forced, force_warning = logout(settings=settings, revoke_remote=failing_revoke, force=True)
    assert forced is not None and forced.token == "active-token"
    assert force_warning == "Unreachable portal"
    assert load_session(settings) is None


def test_logout_with_force_deletes_token_when_revoke_times_out(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    entries: dict[tuple[str, str], str] = {}

    def get(service: str, account: str) -> str | None:
        return entries.get((service, account))

    def save(service: str, account: str, value: str) -> None:
        entries[service, account] = value

    def delete(service: str, account: str) -> None:
        entries.pop((service, account), None)

    monkeypatch.setattr(keyring, "get_password", get)
    monkeypatch.setattr(keyring, "set_password", save)
    monkeypatch.setattr(keyring, "delete_password", delete)

    store_token("timeout-token", settings=settings)

    def timing_out_revoke(_token: str) -> None:
        raise TimeoutError("timed out")

    # Without force, keeps the token
    previous, warning = logout(settings=settings, revoke_remote=timing_out_revoke, force=False)
    assert previous is not None and previous.token == "timeout-token"
    assert warning == "The portal cannot be reached"
    assert load_session(settings) is not None

    forced, force_warning = logout(settings=settings, revoke_remote=timing_out_revoke, force=True)
    assert forced is not None and forced.token == "timeout-token"
    assert force_warning == "The portal cannot be reached"
    assert load_session(settings) is None


def test_logout_with_force_deletes_token_when_revoke_resets_connection(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    entries: dict[tuple[str, str], str] = {}

    def get(service: str, account: str) -> str | None:
        return entries.get((service, account))

    def save(service: str, account: str, value: str) -> None:
        entries[service, account] = value

    def delete(service: str, account: str) -> None:
        entries.pop((service, account), None)

    monkeypatch.setattr(keyring, "get_password", get)
    monkeypatch.setattr(keyring, "set_password", save)
    monkeypatch.setattr(keyring, "delete_password", delete)

    store_token("reset-token", settings=settings)

    def resetting_revoke(_token: str) -> None:
        raise ConnectionResetError("Connection reset by peer")

    # Without force, keeps the token
    previous, warning = logout(settings=settings, revoke_remote=resetting_revoke, force=False)
    assert previous is not None and previous.token == "reset-token"
    assert warning == "The portal cannot be reached"
    assert load_session(settings) is not None

    # With force, deletes the token
    forced, force_warning = logout(settings=settings, revoke_remote=resetting_revoke, force=True)
    assert forced is not None and forced.token == "reset-token"
    assert force_warning == "The portal cannot be reached"
    assert load_session(settings) is None


class FakeWinError(Exception):
    """Shaped like pywintypes.error, which is what the Windows keyring backend raises."""

    def __init__(self, winerror: int, funcname: str, strerror: str) -> None:
        super().__init__(winerror, funcname, strerror)
        self.winerror = winerror
        self.funcname = funcname
        self.strerror = strerror


NO_LOGON_SESSION = FakeWinError(
    1312,
    "CredRead",
    "A specified logon session does not exist. It may already have been terminated.",
)


def _no_logon_session(*_args: str) -> None:
    raise NO_LOGON_SESSION


def test_windows_keychain_failure_is_explained_and_never_falls_back_to_a_file(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "cffi", raising=False)
    monkeypatch.setattr(keyring, "set_password", _no_logon_session)

    with pytest.raises(GardenError) as raised:
        store_token("token", settings=settings)

    error = raised.value
    assert error.title == "Windows Credential Manager is not available in this session"
    assert error.detail is not None
    assert "CredRead failed with Windows error 1312" in error.detail
    assert "remote or background session" in error.detail
    assert error.fix is not None
    assert "normal desktop session" in error.fix
    # The login is never kept in plain text instead.
    assert not (settings.home / "credentials.json").exists()


def test_windows_keychain_failure_on_read_is_explained(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "cffi", raising=False)
    monkeypatch.setattr(keyring, "get_password", _no_logon_session)

    with pytest.raises(GardenError) as raised:
        load_session(settings)
    assert raised.value.title == "Windows Credential Manager is not available in this session"


def test_keychain_error_is_explained_for_the_linux_secret_service(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    def refused(*_args: str) -> None:
        raise KeyringError("no Secret Service answered on the session bus")

    monkeypatch.setattr(keyring, "set_password", refused)

    with pytest.raises(GardenError) as raised:
        store_token("token", settings=settings)

    assert raised.value.title == "The Secret Service keyring is not available in this session"
    assert raised.value.detail is not None
    assert "no Secret Service answered" in raised.value.detail
    assert raised.value.fix is not None
    assert "GNOME Keyring" in raised.value.fix


def test_keychain_delete_failure_is_explained(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(keyring, "delete_password", _no_logon_session)

    with pytest.raises(GardenError) as raised:
        KeyringStore().delete(settings.portal_url)
    assert raised.value.title == "Windows Credential Manager is not available in this session"


def test_a_keychain_that_cannot_be_loaded_is_explained_not_raised_bare(
    monkeypatch: pytest.MonkeyPatch, settings: Settings
) -> None:
    def broken_backend() -> object:
        raise FakeWinError(1312, "CredRead", "A specified logon session does not exist.")

    monkeypatch.setattr(keyring, "get_keyring", broken_backend)
    monkeypatch.setattr(sys, "platform", "win32")

    with pytest.raises(GardenError) as raised:
        store_token("token", settings=settings)
    assert raised.value.title == "Windows Credential Manager is not available in this session"
    assert not (settings.home / "credentials.json").exists()
