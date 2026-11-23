"""Payout dates.

The docs only show one example, "02-05-26 21:12:00 GMT", and do not say whether the day or
the month comes first. So the order is worked out from the data and never assumed silently.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

FORMATS = {"dmy": "%d-%m-%y %H:%M:%S", "mdy": "%m-%d-%y %H:%M:%S"}
DEFAULT_ORDER = "dmy"
_SHORT = re.compile(r"\d\d-\d\d-\d\d \d\d:\d\d:\d\d")


def _clean(raw: str) -> str:
    return re.sub(r"\s*(GMT|UTC)$", "", raw.strip())


def parse_date(raw: str, order: str = DEFAULT_ORDER) -> datetime | None:
    """UTC datetime, or None if the text is not a date in this order."""
    text = _clean(raw)
    try:
        if _SHORT.fullmatch(text):
            return datetime.strptime(text, FORMATS[order]).replace(tzinfo=timezone.utc)
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def detect_order(raws: list[str], now: datetime) -> str | None:
    """'dmy' or 'mdy' if only one of them gives valid dates that are not in the future, else None."""
    short = [r for r in raws if _SHORT.fullmatch(_clean(r))]
    limit = now + timedelta(days=1)

    def fits(order: str) -> bool:
        parsed = [parse_date(r, order) for r in short]
        return all(d is not None and d <= limit for d in parsed)

    ok = [o for o in FORMATS if fits(o)]
    return ok[0] if short and len(ok) == 1 else None
