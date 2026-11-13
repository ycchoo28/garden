"""The end-to-end run's helpers for the model step (ADR 0057): the dsh patch it runs with,
and the CLI login it reads for the calls. Nothing here needs the portal."""

from __future__ import annotations

import importlib
import json
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from garden.dsh import render_patch
from tests.shared.paths import AGENT_ROOT

TEMPLATE = AGENT_ROOT / "install" / "dsh" / "cordis.patch.yml"
GATEWAY = "http://portal.test/api/agent/model/v1"


@pytest.fixture
def e2e() -> Iterator[ModuleType]:
    # The run module re-exports the shared helpers the tests call (dsh_overlay, read_token, PORTAL).
    yield importlib.import_module("tests.e2e.run")


def _installed_patch(path: Path) -> Path:
    """The patch the installer writes: the real template, filled as `garden dsh-config` fills it."""
    text = render_patch(
        TEMPLATE.read_text(encoding="utf-8"),
        garden_command="/x/garden",
        base_url=GATEWAY,
        model="deepseek-v4-flash",
    )
    path.write_text(text, encoding="utf-8")
    return path


def test_the_overlay_gives_the_mcp_server_the_test_store_and_keeps_the_gateway(
    e2e: ModuleType, tmp_path: Path
) -> None:
    patch = _installed_patch(tmp_path / "cordis.patch.yml")

    overlay_path = e2e.dsh_overlay(
        Path("/x/garden-launcher"), tmp_path / "secrets.json", patch, tmp_path
    )
    overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))

    server = next(row for row in overlay[0]["insert"] if row["id"] == "mcp-garden")
    assert server["config"]["command"] == "/x/garden-launcher"
    assert server["config"]["env"] == {"GARDEN_SECRET_STORE": f"file:{tmp_path / 'secrets.json'}"}
    provider = next(row for row in overlay[0]["insert"] if row["id"] == "llm-pi-ai")
    provider_config = provider["config"]["providers"]["garden-portal"]
    assert provider_config["apiKeyEnv"] == "GARDEN_AGENT_MODEL_TOKEN"
    assert provider_config["baseURL"] == GATEWAY
    assert overlay[1]["config"]["model"] == "deepseek-v4-flash"


def test_the_overlay_holds_no_token(e2e: ModuleType, tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.json"
    secrets.write_text(
        json.dumps({e2e.PORTAL: json.dumps({"token": "tok-secret-123", "device": "d"})}),
        encoding="utf-8",
    )
    patch = _installed_patch(tmp_path / "cordis.patch.yml")

    overlay_path = e2e.dsh_overlay(Path("/x/garden-launcher"), secrets, patch, tmp_path)

    text = overlay_path.read_text(encoding="utf-8")
    assert "tok-secret-123" not in text
    assert "GARDEN_AGENT_MODEL_TOKEN" in text  # the variable's name only, read by dsh per request


def test_the_token_is_read_from_the_test_store(e2e: ModuleType, tmp_path: Path) -> None:
    store = tmp_path / "secrets.json"
    login = json.dumps({"token": "abc123", "device": "d"})
    store.write_text(json.dumps({e2e.PORTAL: login}), encoding="utf-8")

    assert e2e.read_token(store) == "abc123"
