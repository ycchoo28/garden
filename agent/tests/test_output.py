"""Table cells: long values are cut to a short ASCII mark, so Windows consoles show them cleanly."""

from __future__ import annotations

import io

from garden.shared import MAX_CELL, render


def test_a_long_table_cell_is_cut_with_an_ascii_mark_and_stays_within_the_width() -> None:
    out = io.StringIO()

    render([{"name": "x" * (MAX_CELL + 20)}], as_json=False, out=out)

    cells = [line.strip() for line in out.getvalue().splitlines() if line.strip().startswith("xxx")]
    assert cells, out.getvalue()
    assert cells[0].endswith("...")
    assert len(cells[0]) <= MAX_CELL
    assert out.getvalue().isascii()


def test_a_single_record_shows_a_long_value_in_full() -> None:
    out = io.StringIO()
    note = "The CLI login still exists until the person revokes it under CLI logins."

    render({"note": note}, as_json=False, out=out)

    assert note in out.getvalue()
    assert out.getvalue().isascii()
