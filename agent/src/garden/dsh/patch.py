"""dsh patch configuration, pointed at the portal's model gateway (ADR 0057).

The model is the portal's. dsh talks to `/api/agent/model/v1` with the person's CLI login, which the
`garden dsh` command reads from the keychain each time it starts dsh and passes to dsh's process
only, in the environment. The home patch that dsh reads names the gateway, the default model and
the variable that holds the token, but never the token itself, so nothing secret reaches the disk.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from pathlib import Path

from garden.engine import PortalClient
from garden.shared import (
    GardenError,
    Settings,
    as_object,
    load_settings,
)

PROVIDER = "garden-portal"
# The environment variable dsh reads the token from, on each request.
TOKEN_ENV = "GARDEN_AGENT_MODEL_TOKEN"
GATEWAY_PATH = "/api/agent/model/v1"
MODELS_PATH = f"{GATEWAY_PATH}/models"
TEMPLATE_NAME = "dsh-config.yml"
PATCH_NAME = "cordis.patch.yml"
# The MCP server's arguments when the installer wrote none: `garden mcp`.
DEFAULT_GARDEN_ARGS = ("mcp",)
# A model id as the portal writes it, such as `deepseek-v4-flash` or `vendor/model:free`. Anything
# else is refused before it reaches the patch.
MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:+-]{0,199}$")


def gateway_url(portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}{GATEWAY_PATH}"


def default_model(client: PortalClient) -> str:
    """The model the portal names as `default`. The portal decides, not the laptop."""
    response = (
        client.call("GET", MODELS_PATH)
        if hasattr(client, "call")
        else client.request("GET", MODELS_PATH)
    )
    raw = getattr(response, "data", getattr(response, "body", response))
    body = as_object(raw)
    name = body.get("default")
    if not isinstance(name, str) or not MODEL_ID.match(name):
        raise GardenError(
            title="The portal named a model dsh cannot use",
            detail=f"The default model is {name!r}.",
            fix="Ask an agent admin to check the model settings in Garden, then try again.",
        )
    return name


def render_patch(
    template: str,
    *,
    garden_command: str,
    base_url: str,
    model: str,
    garden_args: Sequence[str] = DEFAULT_GARDEN_ARGS,
) -> str:
    """Fill the template. Values are written as JSON, so no value can break the YAML.

    `garden_args` are the arguments of the MCP server's command. On Windows the command is Python
    and the first argument is the zipapp, so they are listed here rather than in the template.
    """
    values = {
        "{{GARDEN_COMMAND}}": garden_command,
        "{{MODEL_BASE_URL}}": base_url,
        "{{MODEL_PROVIDER}}": PROVIDER,
        "{{MODEL_NAME}}": model,
        "{{MODEL_KEY_ENV}}": TOKEN_ENV,
    }
    text = template
    for placeholder, value in values.items():
        text = text.replace(placeholder, json.dumps(value)[1:-1])
    # Each argument is a JSON string, so the list is a valid YAML flow sequence.
    arguments = ", ".join(json.dumps(argument) for argument in garden_args)
    return text.replace("{{GARDEN_ARGS}}", arguments)


def write_patch(
    target_or_template: Path,
    target: Path | None = None,
    *,
    portal_url: str | None = None,
    base_url: str | None = None,
    model: str,
    garden_command: str | None = None,
    garden_args: tuple[str, ...] | None = None,
    template_file: Path | None = None,
    settings: Settings | None = None,
) -> Path:
    if target is not None:
        template = target_or_template
        dest = target
        effective_base_url = base_url or (gateway_url(portal_url) if portal_url else "")
        effective_cmd = garden_command or "garden"
        effective_args = garden_args if garden_args is not None else DEFAULT_GARDEN_ARGS
    else:
        active_settings = settings or load_settings()
        dest = (
            target_or_template
            if target_or_template.name == PATCH_NAME
            else target_or_template / PATCH_NAME
        )
        template = template_file or (active_settings.home / TEMPLATE_NAME)
        effective_base_url = base_url or (gateway_url(portal_url or active_settings.portal_url))
        effective_cmd = garden_command or active_settings.garden_command
        effective_args = garden_args if garden_args is not None else active_settings.garden_args

    if not template.exists():
        raise GardenError(
            title="dsh's config template is missing",
            detail=f"No file at {template}.",
            fix="Run the installer again, so the template is fetched.",
        )
    text = render_patch(
        template.read_text(encoding="utf-8"),
        garden_command=effective_cmd,
        base_url=effective_base_url,
        model=model,
        garden_args=effective_args,
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")
    return dest
