"""Reads a company card statement CSV and checks every row, without touching the database.

The check is a pure function of the file and the facts it is given: the department ids, the closed
months and the transaction references already saved. The service supplies those facts, so the dry
run and the import apply the same rules.
"""

import csv
import io
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.shared.money import parse_money

HEADER: tuple[str, ...] = (
    "transaction_ref",
    "posted_on",
    "department_code",
    "category",
    "merchant",
    "amount_usd",
)
# The fixed spend categories. Budget lines use the same list, so spend and budgets line up.
CATEGORIES: tuple[str, ...] = (
    "equipment",
    "marketing",
    "meals",
    "office",
    "other",
    "services",
    "software",
    "travel",
)
MAX_BYTES = 1_000_000
MAX_ROWS = 5_000
REFERENCE_MAX = 120
MERCHANT_MAX = 200
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


class FileRejected(Exception):
    """The whole file cannot be read, so no row can be checked. The message says why."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class CardRow:
    row: int
    transaction_ref: str
    posted_on: date
    department_id: UUID
    category: str
    merchant: str
    amount: Decimal

    @property
    def year_month(self) -> str:
        return f"{self.posted_on.year:04d}-{self.posted_on.month:02d}"


@dataclass(frozen=True)
class Issue:
    row: int
    field: str
    message: str
    transaction_ref: str | None = None


@dataclass(frozen=True)
class Check:
    rows_read: int
    rows: tuple[CardRow, ...]
    errors: tuple[Issue, ...]
    duplicates: tuple[Issue, ...]

    @property
    def total_amount(self) -> Decimal:
        return sum((row.amount for row in self.rows), Decimal("0.00"))

    @property
    def can_apply(self) -> bool:
        """A statement is saved only when it has rows and no row is an error or a duplicate."""
        return self.rows_read > 0 and not self.errors and not self.duplicates


@dataclass(frozen=True)
class _Line:
    row: int
    cells: Sequence[str]


def _read_lines(data: bytes) -> list[_Line]:
    if len(data) > MAX_BYTES:
        raise FileRejected(f"A statement may be at most {MAX_BYTES} bytes.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise FileRejected("The statement must be UTF-8 text.") from error
    try:
        records = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as error:
        raise FileRejected(f"The statement is not valid CSV: {error}.") from error
    if not records or [cell.strip() for cell in records[0]] != list(HEADER):
        raise FileRejected(f"The first line must be exactly: {','.join(HEADER)}.")
    data_records = [record for record in records[1:] if any(cell.strip() for cell in record)]
    if not data_records:
        raise FileRejected("The statement has no rows.")
    if len(data_records) > MAX_ROWS:
        raise FileRejected(f"A statement may have at most {MAX_ROWS} rows.")
    return [_Line(row=number, cells=record) for number, record in enumerate(data_records, start=1)]


def candidate_refs(data: bytes) -> list[str]:
    """The transaction references in the file, so the service can look up the ones already saved."""
    refs: set[str] = set()
    for line in _read_lines(data):
        if line.cells:
            reference = line.cells[0].strip()
            if reference:
                refs.add(reference)
    return sorted(refs)


def _parse_row(line: _Line, departments: Mapping[str, UUID]) -> tuple[list[Issue], CardRow | None]:
    cells = [cell.strip() for cell in line.cells]
    if len(cells) != len(HEADER):
        return [
            Issue(line.row, "row", f"Expected {len(HEADER)} columns, found {len(cells)}.")
        ], None
    reference, posted, department_code, category, merchant, amount_text = cells
    issues: list[Issue] = []
    if not reference or len(reference) > REFERENCE_MAX:
        issues.append(
            Issue(
                line.row,
                "transaction_ref",
                "Each row needs a transaction reference of at most 120 characters.",
            )
        )
    posted_on: date | None = None
    if _DATE.fullmatch(posted):
        try:
            posted_on = date.fromisoformat(posted)
        except ValueError:
            posted_on = None
    if posted_on is None:
        issues.append(Issue(line.row, "posted_on", "Use a real date in YYYY-MM-DD form."))
    department_id = departments.get(department_code)
    if department_id is None:
        issues.append(
            Issue(line.row, "department_code", f"Unknown department code '{department_code}'.")
        )
    if category not in CATEGORIES:
        issues.append(
            Issue(line.row, "category", f"Category must be one of: {', '.join(CATEGORIES)}.")
        )
    if not merchant or len(merchant) > MERCHANT_MAX:
        issues.append(
            Issue(line.row, "merchant", "Each row needs a merchant of at most 200 characters.")
        )
    amount: Decimal | None = None
    try:
        amount = parse_money(amount_text)
    except ValueError as error:
        issues.append(Issue(line.row, "amount_usd", str(error)))
    if issues or posted_on is None or department_id is None or amount is None:
        return issues, None
    return [], CardRow(
        row=line.row,
        transaction_ref=reference,
        posted_on=posted_on,
        department_id=department_id,
        category=category,
        merchant=merchant,
        amount=amount,
    )


def check(
    data: bytes,
    *,
    departments: Mapping[str, UUID],
    closed_months: frozenset[str],
    existing: Mapping[str, str],
) -> Check:
    """Check every row. `existing` maps saved references to the statement holding them."""
    rows: list[CardRow] = []
    errors: list[Issue] = []
    duplicates: list[Issue] = []
    first_seen: dict[str, int] = {}
    lines = _read_lines(data)
    for line in lines:
        row_issues, card = _parse_row(line, departments)
        errors.extend(row_issues)
        reference = line.cells[0].strip() if line.cells else ""
        if reference:
            if reference in first_seen:
                duplicates.append(
                    Issue(
                        line.row,
                        "transaction_ref",
                        f"Duplicate of row {first_seen[reference]} in this file.",
                        reference,
                    )
                )
            else:
                first_seen[reference] = line.row
                if reference in existing:
                    duplicates.append(
                        Issue(
                            line.row,
                            "transaction_ref",
                            f"Already imported in statement {existing[reference]}.",
                            reference,
                        )
                    )
        if card is None:
            continue
        if card.year_month in closed_months:
            errors.append(
                Issue(
                    line.row,
                    "posted_on",
                    f"The month {card.year_month} is closed, so this row cannot be saved.",
                )
            )
            continue
        rows.append(card)
    return Check(
        rows_read=len(lines),
        rows=tuple(rows),
        errors=tuple(errors),
        duplicates=tuple(duplicates),
    )
