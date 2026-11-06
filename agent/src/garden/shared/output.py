"""Plain text tables and JSON for people and for agents. Nothing here is colour or terminal
control."""

from __future__ import annotations

import json
import sys
from typing import Any, TextIO

from garden.shared.errors import GardenError
from garden.shared.json_types import as_list, is_list, is_object

MAX_COLUMNS = 6
MAX_CELL = 48


def render(value: Any, *, as_json: bool, out: TextIO = sys.stdout) -> None:
    if as_json:
        out.write(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
        return
    if value is None:
        out.write("Done.\n")
        return
    if is_object(value) and is_list(value.get("items")):
        items = as_list(value.get("items"))
        _write_table(items, out)
        total = value.get("total")
        if total is not None:
            out.write(f"{len(items)} of {total}\n")
        return
    if is_list(value):
        _write_table(value, out)
        return
    if is_object(value):
        _write_record(value, out)
        return
    out.write(f"{value}\n")


def render_error(
    error: GardenError, *, as_json: bool, out: TextIO = sys.stdout, err: TextIO = sys.stderr
) -> None:
    if as_json:
        out.write(json.dumps(error.to_dict(), indent=2, ensure_ascii=False) + "\n")
        return
    line = f"error: {error.title}"
    if error.detail:
        line += f": {error.detail}"
    err.write(line + "\n")
    for item in error.errors:
        err.write(f"  {item.get('field', '')}: {item.get('message', '')}\n")
    if error.fix:
        err.write(f"fix: {error.fix}\n")


def _cell(value: Any, *, limit: int | None = MAX_CELL) -> str:
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "yes" if value else "no"
    elif isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = text.replace("\n", " ")
    # ASCII only: Windows PowerShell shows a non-ASCII character as garbage in its default console.
    if limit is None or len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _columns(rows: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key in seen or isinstance(value, dict | list):
                continue
            seen.append(key)
    if "id" in seen:
        seen.remove("id")
        seen.insert(0, "id")
    return seen[:MAX_COLUMNS]


def _write_table(items: list[Any], out: TextIO) -> None:
    if not items:
        out.write("No rows.\n")
        return
    if not all(isinstance(item, dict) for item in items):
        for item in items:
            out.write(f"{item}\n")
        return
    rows: list[dict[str, Any]] = items
    columns = _columns(rows)
    cells = [[_cell(row.get(column)) for column in columns] for row in rows]
    widths = [
        max(len(column), *(len(line[index]) for line in cells))
        for index, column in enumerate(columns)
    ]
    out.write(
        "  ".join(column.ljust(widths[index]) for index, column in enumerate(columns)).rstrip()
        + "\n"
    )
    out.write("  ".join("-" * width for width in widths) + "\n")
    for line in cells:
        out.write(
            "  ".join(cell.ljust(widths[index]) for index, cell in enumerate(line)).rstrip() + "\n"
        )


def _write_record(record: dict[str, Any], out: TextIO) -> None:
    if not record:
        out.write("Done.\n")
        return
    width = max(len(key) for key in record)
    for key, value in record.items():
        # A single record shows each value in full, so a note the person must read is not cut.
        out.write(f"{key.ljust(width)}  {_cell(value, limit=None)}\n")
