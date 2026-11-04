"""Money as exact decimals with two places, matching the NUMERIC(14,2) columns that store it."""

import re
from decimal import Decimal

CENTS = Decimal("0.01")
# Up to twelve whole-dollar digits and at most two cent digits, with an optional minus sign.
_MONEY = re.compile(r"-?\d{1,12}(\.\d{1,2})?")


def parse_money(text: str) -> Decimal:
    """Read an amount such as "1200", "1200.5" or "-35.00". Raise ValueError for anything else."""
    cleaned = text.strip()
    if _MONEY.fullmatch(cleaned) is None:
        raise ValueError("Enter an amount in dollars with at most two decimals, such as 1200.00.")
    return Decimal(cleaned).quantize(CENTS)


def format_money(value: Decimal) -> str:
    """Write an amount as the API sends it: a string with exactly two decimals."""
    return str(value.quantize(CENTS) + Decimal("0.00"))
