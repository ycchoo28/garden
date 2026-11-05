"""Months are written as YYYY-MM. That text sorts in calendar order, so ranges compare as text."""

import re

YEAR_MONTH_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
_YEAR_MONTH = re.compile(YEAR_MONTH_PATTERN)


def is_year_month(value: str) -> bool:
    return _YEAR_MONTH.fullmatch(value) is not None
