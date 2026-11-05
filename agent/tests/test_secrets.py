"""The keychain store: on Windows, cffi is hidden so pywin32-ctypes uses its ctypes backend."""

import importlib
import sys

import pytest

from garden import secrets


def test_windows_hides_cffi_so_pywin32_ctypes_uses_its_ctypes_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "cffi", raising=False)

    secrets.KeyringStore()

    with pytest.raises(ImportError):
        importlib.import_module("cffi")


def test_linux_leaves_cffi_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delitem(sys.modules, "cffi", raising=False)

    secrets.KeyringStore()

    assert "cffi" not in sys.modules


def test_default_store_hides_cffi_before_it_asks_keyring_for_a_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # keyring picks its Windows backend by importing pywin32-ctypes. With cffi importable,
    # that import can fail, and the CLI would silently keep the login in a plain file.
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("GARDEN_SECRET_STORE", raising=False)
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

    monkeypatch.setattr(secrets.keyring, "get_keyring", backend_for_this_system)

    store = secrets.default_store()

    assert cffi_hidden_when_asked == [True]
    assert isinstance(store, secrets.KeyringStore)
