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
