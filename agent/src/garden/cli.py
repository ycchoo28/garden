"""The `garden` command. People get flags, tables and prompts; agents get --json and JSON errors.

Commands for the portal are generated from the contract (see engine.py and tools.py), so a route
the person may use is a command here without any code change.
"""

from __future__ import annotations

import argparse
import importlib.resources
import json
import sys
from collections.abc import Sequence
from functools import partial
from pathlib import Path
from typing import Any

from garden import __version__, auth
from garden.config import Settings, load_settings
from garden.engine import Engine
from garden.errors import GardenError, NotSignedIn
from garden.jsonio import as_object, is_object
from garden.login import DEFAULT_TIMEOUT_SECONDS, Login, device_name, run_login
from garden.output import render, render_error
from garden.portal import PortalClient
from garden.secrets import SecretStore, default_store
from garden.tools import Param, Tool

RESERVED_FLAGS = {"json", "body", "help"}


class _Context:
    def __init__(self, store: SecretStore) -> None:
        self.store = store
        self._settings: Settings | None = None

    @property
    def settings(self) -> Settings:
        """Loaded on first use, so `--help` and `--version` work before a portal address is set."""
        if self._settings is None:
            self._settings = load_settings()
        return self._settings

    def login(self) -> Login:
        stored = auth.load(self.store, self.settings.portal_url)
        if stored is None:
            raise NotSignedIn()
        return stored

    def engine(self) -> Engine:
        """An engine for this device. Without a login its portal calls fail with `Not signed in`."""
        stored = auth.load(self.store, self.settings.portal_url)
        client = PortalClient(self.settings.portal_url, token=stored.token if stored else None)
        return Engine(client, self.settings.cache_dir)


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args_list
    try:
        context = _Context(default_store())
        parser, tree = _static_parser(context)
        if args_list and args_list[0] not in _LOCAL_COMMANDS:
            _add_generated(tree, context)
        args = parser.parse_args(args_list)
        return int(args.handler(args) or 0)
    except GardenError as error:
        render_error(error, as_json=as_json)
        return 1
    except KeyboardInterrupt:
        return 130


# Commands that never need the portal's contract, so they run without a network call.
_LOCAL_COMMANDS = {
    "login",
    "logout",
    "whoami",
    "tools",
    "mcp",
    "skills",
    "-h",
    "--help",
    "--version",
    "-V",
}


def _static_parser(context: _Context) -> tuple[argparse.ArgumentParser, _Tree]:
    parser = argparse.ArgumentParser(
        prog="garden", description="The Garden portal from the terminal and for agents."
    )
    parser.add_argument("--version", action="version", version=f"garden {__version__}")
    tree = _Tree(parser)

    login = tree.add(("login",), "Sign this device in through the browser.")
    login.add_argument(
        "--device",
        default=device_name(),
        help="The name this device shows in Garden and the audit trail.",
    )
    login.add_argument(
        "--no-open",
        action="store_true",
        help="Print the sign-in link instead of opening a browser.",
    )
    login.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    login.set_defaults(handler=partial(_login, context))

    whoami = tree.add(("whoami",), "Show the person this device is signed in as.")
    whoami.add_argument("--json", action="store_true")
    whoami.set_defaults(handler=partial(_whoami, context))

    logout = tree.add(("logout",), "Remove this device's CLI login. Other devices keep working.")
    logout.add_argument("--json", action="store_true")
    logout.set_defaults(handler=partial(_logout, context))

    tools = tree.add(("tools",), "List the tools you may use, and which need approval.")
    tools.add_argument("--json", action="store_true")
    tools.set_defaults(handler=partial(_tools, context))

    mcp = tree.add(("mcp",), "Serve the tools to an agent over MCP on stdin and stdout.")
    mcp.set_defaults(handler=partial(_mcp, context))

    install = tree.add(
        ("skills", "install"), "Copy the SKILL.md files into a folder for agents that read skills."
    )
    install.add_argument(
        "--to", type=Path, help="The folder to copy into. Default: ~/.garden/skills."
    )
    install.set_defaults(handler=partial(_skills_install, context))
    return parser, tree


def _login(context: _Context, args: argparse.Namespace) -> None:
    login = run_login(
        context.settings.portal_url,
        args.device,
        open_browser=not args.no_open,
        timeout=args.timeout,
    )
    auth.save(context.store, context.settings.portal_url, login)
    try:
        me = context.engine().me()
    except GardenError as error:
        print(f"Signed in on {login.device}.")
        print(
            f"The portal did not confirm who you are: {error.title}. Run garden whoami to check.",
            file=sys.stderr,
        )
        return
    print(f"Signed in as {me.get('name', '')} ({me.get('email', '')}) on {login.device}.")


def _whoami(context: _Context, args: argparse.Namespace) -> None:
    stored = context.login()
    me = context.engine().me()
    record: dict[str, Any] = {
        "name": me.get("name"),
        "email": me.get("email"),
        "device": stored.device,
        "permissions": me.get("permissions", []),
    }
    render(record, as_json=args.json)


def _logout(context: _Context, args: argparse.Namespace) -> None:
    # The login is revoked in the browser by the person, so this device only forgets its copy.
    stored = auth.load(context.store, context.settings.portal_url)
    auth.clear(context.store, context.settings.portal_url)
    render(
        {
            "signed_out": True,
            "device": stored.device if stored else None,
            "note": "The CLI login still exists until the person revokes it under CLI logins.",
        },
        as_json=args.json,
    )


def _tools(context: _Context, args: argparse.Namespace) -> None:
    tools = context.engine().tools()
    rows = [
        {
            "name": tool.name,
            "method": tool.method.upper(),
            "permission": tool.permission,
            "approval": "needed" if tool.critical else "no",
        }
        for tool in tools
    ]
    render(rows, as_json=args.json)


def _mcp(context: _Context, args: argparse.Namespace) -> None:
    from garden.mcp import serve

    serve(context.engine(), sys.stdin, sys.stdout)


def _skills_install(context: _Context, args: argparse.Namespace) -> None:
    source = importlib.resources.files("garden") / "skills"
    target: Path = args.to or context.settings.home / "skills"
    installed: list[str] = []
    for skill in sorted(source.iterdir(), key=lambda item: item.name):
        if not skill.is_dir():
            continue
        destination = target / skill.name
        destination.mkdir(parents=True, exist_ok=True)
        # Read through the resource API, so the copy also works when the package is zipped.
        (destination / "SKILL.md").write_bytes((skill / "SKILL.md").read_bytes())
        installed.append(skill.name)
    print(f"Installed {', '.join(installed)} into {target}")


def _add_generated(tree: _Tree, context: _Context) -> None:
    """Add a command for each tool the person may use."""
    for tool in context.engine().tools():
        path = tuple(tool.op_id.split("."))
        if tree.exists(path):
            continue
        leaf = tree.add(path, tool.summary)
        _tool_arguments(leaf, tool)
        leaf.set_defaults(handler=partial(_run_tool, context, tool))


class _Tree:
    """Command groups for the dotted operation ids. Every group and leaf is created once, here."""

    def __init__(self, root: argparse.ArgumentParser) -> None:
        self._parsers: dict[tuple[str, ...], argparse.ArgumentParser] = {(): root}
        self._subs: dict[tuple[str, ...], Any] = {}

    def exists(self, path: tuple[str, ...]) -> bool:
        return path in self._parsers

    def add(self, path: tuple[str, ...], help_text: str) -> argparse.ArgumentParser:
        parser = self._subparsers(path[:-1]).add_parser(path[-1], help=help_text)
        self._parsers[path] = parser
        return parser

    def _group(self, path: tuple[str, ...]) -> argparse.ArgumentParser:
        if path not in self._parsers:
            group = self._subparsers(path[:-1]).add_parser(path[-1], help=f"{path[-1]} commands")
            self._parsers[path] = group
        return self._parsers[path]

    def _subparsers(self, path: tuple[str, ...]) -> Any:
        if path not in self._subs:
            subs = self._group(path).add_subparsers(metavar="command")
            subs.required = True
            self._subs[path] = subs
        return self._subs[path]


def _tool_arguments(parser: argparse.ArgumentParser, tool: Tool) -> None:
    parser.add_argument(
        "--json", action="store_true", help="Print JSON, and JSON errors, for agents."
    )
    for param in (*tool.path_params, *tool.query_params):
        _add_param(parser, param, required=param.required)
    if tool.body_schema is not None:
        for name, schema in as_object(tool.body_schema.get("properties")).items():
            if name in RESERVED_FLAGS:
                continue
            _add_body_field(parser, name, as_object(schema))
        parser.add_argument(
            "--body", help="The whole body as JSON, a file path with @, or - for stdin."
        )


def _add_param(parser: argparse.ArgumentParser, param: Param, *, required: bool) -> None:
    flag = f"--{param.name.replace('_', '-')}"
    parser.add_argument(
        flag,
        dest=param.name,
        required=required,
        help=param.description,
        **_value_options(param.schema),
    )


def _add_body_field(parser: argparse.ArgumentParser, name: str, schema: dict[str, Any]) -> None:
    flag = f"--{name.replace('_', '-')}"
    help_text = schema.get("description", "")
    if schema.get("type") in ("array", "object"):
        help_text = f"{help_text} (JSON)".strip()
    parser.add_argument(flag, dest=f"body_{name}", help=help_text, **_value_options(schema))


def _value_options(schema: dict[str, Any]) -> dict[str, Any]:
    """How argparse reads a value of this schema: a number, a boolean, a choice, or text."""
    kind = schema.get("type")
    if kind == "integer":
        return {"type": int}
    if kind == "number":
        return {"type": float}
    if kind == "boolean":
        return {"type": _bool}
    if schema.get("enum"):
        return {"choices": schema["enum"]}
    return {}


def _bool(text: str) -> bool:
    if text.lower() in {"true", "yes", "1"}:
        return True
    if text.lower() in {"false", "no", "0"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {text!r}")


def _run_tool(context: _Context, tool: Tool, args: argparse.Namespace) -> None:
    arguments: dict[str, Any] = {}
    for param in (*tool.path_params, *tool.query_params):
        arguments[param.name] = getattr(args, param.name, None)
    if tool.body_schema is not None:
        arguments["body"] = _body_from(args, tool)
    result = context.engine().call(tool, arguments, notify=_tell_the_person)
    render(result, as_json=args.json)


def _tell_the_person(text: str) -> None:
    """A critical action waits for the person's approval: show them the link on the terminal."""
    print(text, file=sys.stderr, flush=True)


def _body_from(args: argparse.Namespace, tool: Tool) -> dict[str, Any] | None:
    body: dict[str, Any] = {}
    raw = getattr(args, "body", None)
    if raw:
        body.update(_read_json_arg(raw))
    properties = as_object(as_object(tool.body_schema).get("properties"))
    for name, schema in properties.items():
        value = getattr(args, f"body_{name}", None)
        if value is None:
            continue
        if as_object(schema).get("type") in ("array", "object") and isinstance(value, str):
            body[name] = _parse_json(value, name)
        else:
            body[name] = value
    if not body:
        return None
    return body


def _read_json_arg(raw: str) -> dict[str, Any]:
    if raw == "-":
        text = sys.stdin.read()
    elif raw.startswith("@"):
        text = Path(raw[1:]).read_text(encoding="utf-8")
    else:
        text = raw
    value = _parse_json(text, "--body")
    if not is_object(value):
        raise GardenError(
            title="The body must be a JSON object",
            fix='Pass an object, such as {"field": "value"}.',
        )
    return value


def _parse_json(text: str, name: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise GardenError(
            title=f"{name} is not valid JSON", detail=str(error), fix="Fix the JSON and retry."
        ) from error
