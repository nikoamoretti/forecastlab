"""Calendar-month arithmetic for the country-month panel.

Months are integer ordinals ``year * 12 + (month - 1)``, so consecutive months differ by
one and ``origin + h`` is the month ``h`` steps after ``origin``. Files use ``YYYY-MM``.
"""

from __future__ import annotations

import re

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")

# ViEWS numbers months from 1 = January 1980.
_VIEWS_MONTH_ID_ZERO = 1980 * 12 - 1


def month_ordinal(year: int, month: int) -> int:
    if not 1 <= month <= 12:
        raise ValueError(f"month out of range: {month}")
    return year * 12 + (month - 1)


def parse_month(label: str) -> int:
    match = _MONTH_RE.match(label.strip())
    if not match:
        raise ValueError(f"expected YYYY-MM, got {label!r}")
    return month_ordinal(int(match.group(1)), int(match.group(2)))


def format_month(ordinal: int) -> str:
    year, index = divmod(int(ordinal), 12)
    return f"{year:04d}-{index + 1:02d}"


def month_of_timestamp(value: str) -> int:
    """Month of a UCDP timestamp such as ``1992-03-17 00:00:00.000`` or ``1992-03-17``."""
    match = _DATE_RE.match(value.strip())
    if not match:
        raise ValueError(f"unparseable date: {value!r}")
    return month_ordinal(int(match.group(1)), int(match.group(2)))


def year_of(ordinal: int) -> int:
    return int(ordinal) // 12


def views_month_id(ordinal: int) -> int:
    return int(ordinal) - _VIEWS_MONTH_ID_ZERO


def from_views_month_id(month_id: int) -> int:
    return int(month_id) + _VIEWS_MONTH_ID_ZERO
