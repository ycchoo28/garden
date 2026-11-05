"""Where the CLI keeps its settings and cache. Nothing secret is stored here; the CLI login lives in
the keychain."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from garden.errors import GardenError
from garden.jsonio import as_object

# The command dsh's MCP client starts when the installer did not write one: the one on PATH.
DEFAULT_GARDEN_COMMAND = "garden"
# The arguments that start the MCP server: `garden mcp`. Windows puts the zipapp first.
DEFAULT_GARDEN_ARGS: tuple[str, ...] = ("mcp",)


@dataclass(frozen=True)
class Settings:
    portal_url: str
    home: Path
    dsh_home: Path
    # The garden command dsh starts for its MCP tools. The installer writes the launcher's path.
    garden_command: str = DEFAULT_GARDEN_COMMAND
    garden_args: tuple[str, ...] = DEFAULT_GARDEN_ARGS

    @property
    def cache_dir(self) -> Path:
        return self.home / "cache"


def home_dir() -> Path:
    """`GARDEN_HOME`, else `~/.garden`."""
    override = os.environ.get("GARDEN_HOME")
    return Path(override) if override else Path.home() / ".garden"


def load_settings() -> Settings:
    """The portal address comes from `GARDEN_API_URL`, else from `config.json` the installer wrote.

    The rest of `config.json` is optional and read only when it is there.
    """
    home = home_dir()
    config = _read_config(home / "config.json")
    portal_url = os.environ.get("GARDEN_API_URL")
    if not portal_url:
        if "portal_url" not in config:
            raise GardenError(
                title="No portal address",
                fix="Set GARDEN_API_URL to the portal's address, such as http://127.0.0.1:55000.",
            )
        portal_url = str(config["portal_url"])
    dsh_home = (
        os.environ.get("GARDEN_DSH_HOME") or config.get("dsh_home") or str(Path.home() / ".dsh")
    )
    garden_command = config.get("garden_command") or DEFAULT_GARDEN_COMMAND
    raw_args: object = config.get("garden_args")
    garden_args = DEFAULT_GARDEN_ARGS
    if isinstance(raw_args, list) and raw_args:
        garden_args = tuple(str(arg) for arg in cast("list[object]", raw_args))
    return Settings(
        portal_url=portal_url.rstrip("/"),
        home=home,
        dsh_home=Path(str(dsh_home)),
        garden_command=str(garden_command),
        garden_args=garden_args,
    )


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return as_object(json.loads(path.read_text(encoding="utf-8")))
