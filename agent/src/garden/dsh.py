"""dsh, pointed at the portal's model gateway (ADR 0057).

The model is the portal's. dsh talks to `/api/agent/model/v1` with the person's CLI login, which the
`garden dsh` command reads from the keychain each time it starts dsh and passes to dsh's process
only, in the environment. The home patch that dsh reads names the gateway, the default model and
the variable that holds the token, but never the token itself, so nothing secret reaches the disk.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from garden.errors import GardenError
from garden.jsonio import as_object
from garden.portal import PortalClient

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


def entry_point(home: Path) -> Path:
    """dsh's script, as the installer unpacks the bundle under the garden home."""
    return home / "dsh" / "node_modules" / "@deepseek-ai" / "dsh" / "lib" / "bin.js"


def gateway_url(portal_url: str) -> str:
    return f"{portal_url.rstrip('/')}{GATEWAY_PATH}"


def default_model(client: PortalClient) -> str:
    """The model the portal names as `default`. The portal decides, not the laptop."""
    body = as_object(client.request("GET", MODELS_PATH).body)
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
    garden_args: tuple[str, ...] = DEFAULT_GARDEN_ARGS,
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
    template_file: Path,
    target: Path,
    *,
    garden_command: str,
    base_url: str,
    model: str,
    garden_args: tuple[str, ...] = DEFAULT_GARDEN_ARGS,
) -> None:
    if not template_file.exists():
        raise GardenError(
            title="dsh's config template is missing",
            detail=f"No file at {template_file}.",
            fix="Run the installer again, so the template is fetched.",
        )
    text = render_patch(
        template_file.read_text(encoding="utf-8"),
        garden_command=garden_command,
        base_url=base_url,
        model=model,
        garden_args=garden_args,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


# The profile dsh boots when the person names none. dsh has no default-profile setting and refuses
# to start without a profile, so `garden dsh` passes this one. `headless` is the shipped profile
# that the Garden agent runs dsh with. The home patch applies to every profile.
DEFAULT_PROFILE = "headless"
# Flags that ask dsh for its own help or version. dsh answers them without a profile.
_HELP_AND_VERSION_FLAGS = frozenset({"-h", "--help", "-V", "--version"})


def with_default_profile(dsh_args: list[str]) -> list[str]:
    """dsh's arguments, with the default profile added when the person names none.

    dsh boots the profile named by `--profile`, or by a first argument such as `dsh headless`.
    Plain `dsh`, and `dsh` with flags only, get the default. Help, version and `dsh plugin ...` are
    left as they are, and so is any call that already names a profile.
    """
    if not dsh_args:
        return ["--profile", DEFAULT_PROFILE]
    first = dsh_args[0]
    if first == "plugin" or not first.startswith("-"):
        return list(dsh_args)
    if any(arg == "--profile" or arg.startswith("--profile=") for arg in dsh_args):
        return list(dsh_args)
    if any(arg in _HELP_AND_VERSION_FLAGS for arg in dsh_args):
        return list(dsh_args)
    return ["--profile", DEFAULT_PROFILE, *dsh_args]


def launcher_text(garden_command: str, *, windows: bool) -> str:
    """The `dsh` launcher: it runs `garden dsh`, which reads the login and starts dsh."""
    if windows:
        return f'@echo off\r\n"{garden_command}" dsh %*\r\n'
    return f'#!/usr/bin/env bash\nexec {shlex.quote(garden_command)} dsh "$@"\n'
