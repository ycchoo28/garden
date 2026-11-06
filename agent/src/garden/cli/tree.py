"""OpenAPI Tool-to-argparse command tree compilation and flag parsing."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from garden.engine import FormField, Param, Tool
from garden.shared import GardenError, as_object, is_object

RESERVED_FLAGS = {"json", "body", "help"}


def _kebab(name: str) -> str:
    return name.replace("_", "-")


def _flag_name(name: str) -> str:
    return f"--{_kebab(name)}"


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


def _bool(text: str) -> bool:
    if text.lower() in {"true", "yes", "1"}:
        return True
    if text.lower() in {"false", "no", "0"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {text!r}")


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


def _add_param(parser: argparse.ArgumentParser, param: Param, *, required: bool) -> None:
    flag = _flag_name(param.name)
    parser.add_argument(
        flag,
        dest=param.name,
        required=required,
        help=param.description,
        **_value_options(param.schema),
    )


def _add_body_field(
    parser: argparse.ArgumentParser,
    name: str,
    schema: dict[str, Any],
    field: FormField | None = None,
) -> None:
    flag = _flag_name(name)
    help_text = schema.get("description", "")
    if schema.get("type") in ("array", "object"):
        help_text = f"{help_text} (JSON)".strip()
    parser.add_argument(flag, dest=f"body_{name}", help=help_text, **_value_options(schema))


def _parse_flag_value(text: str, name: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise GardenError(
            title=f"{name} is not valid JSON",
            detail=str(error),
            fix="Fix the JSON and retry.",
        ) from error


def _read_json_arg(raw: str) -> dict[str, Any]:
    if raw == "-":
        text = sys.stdin.read()
    elif raw.startswith("@"):
        text = Path(raw[1:]).read_text(encoding="utf-8")
    else:
        text = raw
    value = _parse_flag_value(text, "--body")
    if not is_object(value):
        raise GardenError(
            title="The body must be a JSON object",
            fix='Pass an object, such as {"field": "value"}.',
        )
    return value


def _body_from(args: argparse.Namespace, tool: Tool) -> dict[str, Any] | None:
    body: dict[str, Any] = {}
    raw: str | None = getattr(args, "body", None)
    if raw:
        body.update(_read_json_arg(raw))
    properties = as_object(as_object(tool.body_schema).get("properties"))
    for name, schema in properties.items():
        value: Any = getattr(args, f"body_{name}", None)
        if value is None:
            continue
        schema_dict = as_object(schema)
        if schema_dict.get("type") in ("array", "object") and isinstance(value, str):
            body[name] = _parse_flag_value(value, f"--{name}")
        else:
            body[name] = value
    if not body:
        return None
    return body


def tool_arguments(parser: argparse.ArgumentParser, tool: Tool) -> None:
    parser.add_argument(
        "--json", action="store_true", help="Print JSON, and JSON errors, for agents."
    )
    for param in tool.params:
        _add_param(parser, param, required=param.required)
    if tool.body_schema is not None:
        props = as_object(tool.body_schema.get("properties"))
        for name, schema in props.items():
            if name in RESERVED_FLAGS:
                continue
            _add_body_field(parser, name, as_object(schema))
        parser.add_argument(
            "--body", help="The whole body as JSON, a file path with @, or - for stdin."
        )


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


def add_generated(
    tree: _Tree,
    tools: Iterable[Tool],
    handler_factory: Callable[[Tool], Callable[[argparse.Namespace], Any]],
) -> None:
    """Add a command for each tool the person may use."""
    for tool in tools:
        path = tuple(tool.op_id.split("."))
        if tree.exists(path):
            continue
        leaf = tree.add(path, tool.summary)
        tool_arguments(leaf, tool)
        leaf.set_defaults(handler=handler_factory(tool))


__all__ = [
    "RESERVED_FLAGS",
    "_Tree",
    "_add_body_field",
    "_add_param",
    "_body_from",
    "_first_line",
    "_flag_name",
    "_kebab",
    "_parse_flag_value",
    "add_generated",
    "tool_arguments",
]
