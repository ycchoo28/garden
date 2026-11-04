"""Where the CLI keeps its settings and cache. Nothing secret is stored here; the CLI login lives in
the keychain."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from garden.errors import GardenError
from garden.jsonio import as_object


@dataclass(frozen=True)
class Settings:
    portal_url: str
    home: Path

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"


def home_dir() -> Path:
    """`GARDEN_HOME`, else `~/.garden`."""
    override = os.environ.get("GARDEN_HOME")
    return Path(override) if override else Path.home() / ".garden"


def load_settings() -> Settings:
    """The portal address comes from `GARDEN_API_URL`, else from `config.json` in home."""
    home = home_dir()
    portal_url = os.environ.get("GARDEN_API_URL")
    if not portal_url:
        config = _read_config(home / "config.json")
        if "portal_url" not in config:
            raise GardenError(
                title="No portal address",
                fix="Set GARDEN_API_URL to the portal's address, such as http://127.0.0.1:55000.",
            )
        portal_url = str(config["portal_url"])
    return Settings(portal_url=portal_url.rstrip("/"), home=home)


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return as_object(json.loads(path.read_text(encoding="utf-8")))
