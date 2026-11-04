"""The CLI login on this device, kept in the keychain under the portal's address."""

from __future__ import annotations

import json
from dataclasses import asdict

from garden.login import Login
from garden.secrets import SecretStore


def save(store: SecretStore, portal_url: str, login: Login) -> None:
    store.set(portal_url, json.dumps(asdict(login)))


def load(store: SecretStore, portal_url: str) -> Login | None:
    raw = store.get(portal_url)
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return Login(token=str(data["token"]), device=str(data.get("device", "")))
    except (ValueError, KeyError, TypeError):
        return None


def clear(store: SecretStore, portal_url: str) -> None:
    store.delete(portal_url)
