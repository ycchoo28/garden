"""Stores the CLI login in the OS keychain: Windows Credential Manager, the macOS keychain or the
Secret Service.

A file is the fallback only when this system has no keychain at all, such as a bare container. The
file is private to the person (mode 0600) and the CLI warns every time it uses it, because a file
holds the token in plain text. `GARDEN_SECRET_STORE=file:<path>` picks a file on purpose, for the
test suite.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Protocol

import keyring
from keyring.backends import fail
from keyring.errors import KeyringError, PasswordDeleteError

from garden.shared import GardenError, home_dir

SERVICE = "garden"
FALLBACK_FILE = "credentials.json"


class SecretStore(Protocol):
    def get(self, account: str) -> str | None: ...

    def set(self, account: str, value: str) -> None: ...

    def delete(self, account: str) -> None: ...


class KeyringStore:
    def __init__(self) -> None:
        _hide_cffi_from_windows_keyring()

    def get(self, account: str) -> str | None:
        try:
            return keyring.get_password(SERVICE, account)
        except KeyringError as error:
            raise _no_keychain(error) from error

    def set(self, account: str, value: str) -> None:
        try:
            keyring.set_password(SERVICE, account, value)
        except KeyringError as error:
            raise _no_keychain(error) from error

    def delete(self, account: str) -> None:
        try:
            keyring.delete_password(SERVICE, account)
        except PasswordDeleteError:
            return
        except KeyringError as error:
            raise _no_keychain(error) from error


class FileStore:
    """A JSON file of account to value, readable only by the person."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        return dict(json.loads(self._path.read_text(encoding="utf-8")))

    def _save(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # The mode applies when the file is created, so the token is never readable by others.
        descriptor = os.open(self._path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            _ = handle.write(json.dumps(data))

    def get(self, account: str) -> str | None:
        return self._load().get(account)

    def set(self, account: str, value: str) -> None:
        data = self._load()
        data[account] = value
        self._save(data)

    def delete(self, account: str) -> None:
        data = self._load()
        data.pop(account, None)
        self._save(data)


def _hide_cffi_from_windows_keyring() -> None:
    """Make pywin32-ctypes use its ctypes backend on Windows.

    The keyring backend loads pywin32-ctypes on first use, and that picks cffi whenever it can
    import it. Hiding cffi makes it fall back to ctypes, which is pure Python.
    """
    if sys.platform == "win32" and "cffi" not in sys.modules:
        sys.modules["cffi"] = None  # type: ignore[assignment]


def _no_keychain(error: Exception) -> GardenError:
    return GardenError(
        title="No keychain available",
        detail=str(error),
        fix="On Linux, start a Secret Service provider (such as GNOME Keyring) and try again.",
    )


def _keychain_missing() -> bool:
    return isinstance(keyring.get_keyring(), fail.Keyring)


def default_store(home: Path | None = None) -> SecretStore:
    # Before keyring is asked for its backend: the choice itself imports pywin32-ctypes.
    _hide_cffi_from_windows_keyring()
    spec = os.environ.get("GARDEN_SECRET_STORE", "")
    if spec.startswith("file:"):
        print(
            "warning: GARDEN_SECRET_STORE=file: keeps the CLI login in plain text. Tests only.",
            file=sys.stderr,
        )
        return FileStore(Path(spec.removeprefix("file:")))
    if _keychain_missing():
        print(
            "warning: no keychain on this system, so the CLI login is kept in a private file "
            "in the garden home folder, in plain text.",
            file=sys.stderr,
        )
        base = home if home is not None else home_dir()
        return FileStore(base / FALLBACK_FILE)
    return KeyringStore()
