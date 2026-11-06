"""The garden command: built-in command handlers and execution dispatch."""

from __future__ import annotations

import argparse
import importlib.resources
import sys
from collections.abc import Callable, Sequence
from functools import partial
from pathlib import Path
from typing import Any

from garden import __version__
from garden.auth import (
    DEFAULT_TIMEOUT_SECONDS,
    device_name,
    login,
    logout,
    require_session,
)
from garden.cli.tree import _body_from, _Tree, add_generated
from garden.dsh import configure, run, write_launcher
from garden.engine import Engine, PortalClient, Tool, connect
from garden.mcp import serve_stdio
from garden.shared import (
    GardenError,
    Settings,
    as_object,
    load_settings,
    render,
    render_error,
)

_LOCAL_COMMANDS = {
    "login",
    "logout",
    "whoami",
    "tools",
    "mcp",
    "skills",
    "dsh",
    "dsh-config",
    "dsh-launcher",
    "-h",
    "--help",
    "--version",
    "-V",
}


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    as_json = "--json" in args_list
    settings_cache: list[Settings] = []

    def get_settings() -> Settings:
        if not settings_cache:
            settings_cache.append(load_settings())
        return settings_cache[0]

    try:
        if args_list[:1] == ["dsh"]:
            # Everything after `dsh` is for dsh, so it is passed on as it is, flags and all.
            return run(args_list[1:], settings=get_settings())
        parser, tree = _static_parser(get_settings)
        if args_list and args_list[0] not in _LOCAL_COMMANDS:
            engine = connect(settings=get_settings())
            add_generated(
                tree,
                engine.tools(),
                lambda tool: partial(_run_tool, engine, tool),
            )
        args = parser.parse_args(args_list)
        return int(args.handler(args) or 0)
    except GardenError as error:
        render_error(error, as_json=as_json)
        return 1
    except KeyboardInterrupt:
        return 130


def _static_parser(
    get_settings: Callable[[], Settings],
) -> tuple[argparse.ArgumentParser, _Tree]:
    parser = argparse.ArgumentParser(
        prog="garden", description="The Garden portal from the terminal and for agents."
    )
    parser.add_argument("--version", action="version", version=f"garden {__version__}")
    tree = _Tree(parser)

    login_cmd = tree.add(("login",), "Sign this device in through the browser.")
    login_cmd.add_argument(
        "--device",
        default=device_name(),
        help="The name this device shows in Garden and the audit trail.",
    )
    login_cmd.add_argument(
        "--no-open",
        action="store_true",
        help="Print the sign-in link instead of opening a browser.",
    )
    login_cmd.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    login_cmd.set_defaults(handler=partial(_login, get_settings=get_settings))

    whoami_cmd = tree.add(("whoami",), "Show the person this device is signed in as.")
    whoami_cmd.add_argument("--json", action="store_true")
    whoami_cmd.set_defaults(handler=partial(_whoami, get_settings=get_settings))

    logout_cmd = tree.add(
        ("logout",), "End this device's CLI login on the portal. Other devices keep working."
    )
    logout_cmd.add_argument("--json", action="store_true")
    logout_cmd.set_defaults(handler=partial(_logout, get_settings=get_settings))

    tools_cmd = tree.add(("tools",), "List the tools you may use, and which need approval.")
    tools_cmd.add_argument("--json", action="store_true")
    tools_cmd.set_defaults(handler=partial(_tools, get_settings=get_settings))

    mcp_cmd = tree.add(("mcp",), "Serve the tools to an agent over MCP on stdin and stdout.")
    mcp_cmd.set_defaults(handler=partial(_mcp, get_settings=get_settings))

    install_cmd = tree.add(
        ("skills", "install"), "Copy the SKILL.md files into a folder for agents that read skills."
    )
    install_cmd.add_argument(
        "--to", type=Path, help="The folder to copy into. Default: ~/.garden/skills."
    )
    install_cmd.set_defaults(handler=partial(_skills_install, get_settings=get_settings))

    run_dsh = tree.add(("dsh",), "Run dsh with the portal's model. Its arguments go to dsh.")
    run_dsh.add_argument("args", nargs=argparse.REMAINDER, help="Arguments for dsh.")
    run_dsh.set_defaults(handler=partial(_dsh_from_parser, get_settings=get_settings))

    config_cmd = tree.add(
        ("dsh-config",), "Write dsh's model patch from the portal's default model, then stop."
    )
    config_cmd.set_defaults(handler=partial(_dsh_config, get_settings=get_settings))

    launcher_cmd = tree.add(("dsh-launcher",), "Write the dsh launcher, which runs `garden dsh`.")
    launcher_cmd.add_argument("--to", type=Path, required=True, help="The launcher file to write.")
    launcher_cmd.add_argument(
        "--garden", required=True, help="The garden command the launcher runs."
    )
    launcher_cmd.set_defaults(handler=partial(_dsh_launcher, get_settings=get_settings))

    return parser, tree


def _login(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> None:
    settings = get_settings()

    def on_url(url: str) -> None:
        print(f"Approve this login for {args.device} in your browser:\n  {url}", flush=True)

    def exchange(path: str, payload: dict[str, object]) -> dict[str, Any]:
        client = PortalClient(settings.portal_url)
        res = client.call("POST", path, body=payload)
        return as_object(res.body)

    record = login(
        settings=settings,
        device=args.device,
        open_browser=not args.no_open,
        timeout_seconds=args.timeout,
        on_authorize_url=on_url,
        exchange_code=exchange,
    )
    engine = connect(portal_url=settings.portal_url, token=record.token, settings=settings)
    try:
        me = engine.me()
    except GardenError as error:
        print(f"Signed in on {record.device}.")
        print(
            f"The portal did not confirm who you are: {error.title}. Run garden whoami to check.",
            file=sys.stderr,
        )
        return
    print(f"Signed in as {me.get('name', '')} ({me.get('email', '')}) on {record.device}.")


def _whoami(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> None:
    settings = get_settings()
    session = require_session(settings)
    engine = connect(settings=settings, token=session.token, portal_url=session.portal_url)
    me = engine.me()
    record: dict[str, Any] = {
        "name": me.get("name"),
        "email": me.get("email"),
        "device": session.device,
        "permissions": me.get("permissions", []),
    }
    render(record, as_json=args.json)


def _logout(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> None:
    settings = get_settings()
    previous, warning = logout(
        settings=settings,
        revoke_remote=lambda token: connect(
            portal_url=settings.portal_url, token=token, settings=settings
        ).revoke_cli_token(),
    )
    if warning:
        print(
            f"warning: the portal did not confirm the sign-out ({warning}). "
            "Removing the login here.",
            file=sys.stderr,
        )
    render(
        {"signed_out": True, "device": previous.device if previous else None},
        as_json=args.json,
    )


def _tools(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> None:
    settings = get_settings()
    engine = connect(settings=settings)
    tools = engine.tools()
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


def _mcp(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> int:
    settings = get_settings()
    engine = connect(settings=settings)
    return serve_stdio(engine)


def _skills_install(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> None:
    settings = get_settings()
    source = importlib.resources.files("garden") / "skills"
    target: Path = args.to or settings.home / "skills"
    installed: list[str] = []
    for skill in sorted(source.iterdir(), key=lambda item: item.name):
        if not skill.is_dir():
            continue
        destination = target / skill.name
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "SKILL.md").write_bytes((skill / "SKILL.md").read_bytes())
        installed.append(skill.name)
    print(f"Installed {', '.join(installed)} into {target}")


def _dsh_config(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> None:
    settings = get_settings()
    target = configure(settings=settings)
    print(f"dsh reads its model from {target}.")


def _dsh_launcher(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> None:
    target: Path = args.to
    write_launcher(target, garden_command=args.garden)
    print(f"The dsh launcher is at {target}. It runs `{args.garden} dsh`.")


def _dsh_from_parser(args: argparse.Namespace, *, get_settings: Callable[[], Settings]) -> int:
    settings = get_settings()
    return run(list(args.args or []), settings=settings)


def _tell_the_person(text: str) -> None:
    """A critical action waits for the person's approval: show them the link on the terminal."""
    print(text, file=sys.stderr, flush=True)


def _run_tool(engine: Engine, tool: Tool, args: argparse.Namespace) -> None:
    arguments: dict[str, Any] = {}
    for param in tool.params:
        arguments[param.name] = getattr(args, param.name, None)
    if tool.body_schema is not None:
        arguments["body"] = _body_from(args, tool)
    result = engine.call(tool, arguments, notify=_tell_the_person)
    render(result, as_json=args.json)
