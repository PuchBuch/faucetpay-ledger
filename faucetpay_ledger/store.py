"""SQLite storage for balance snapshots and the monthly report."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts        TEXT NOT NULL,      -- ISO-8601 UTC
    currency  TEXT NOT NULL,
    amount    TEXT NOT NULL,      -- Decimal as text, whole coins
    fiat      TEXT NOT NULL,      -- e.g. 'eur'
    price     TEXT                -- price of 1 coin in fiat, NULL if unknown
);
CREATE INDEX IF NOT EXISTS ix_snap ON snapshots(currency, ts);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)

    def add(self, currency: str, amount: Decimal, fiat: str, price: Decimal | None, ts: str | None = None):
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?)",
                        (ts or utcnow(), currency, str(amount), fiat, None if price is None else str(price)))
        self.db.commit()

    def _first_last(self, currency: str, start: str, end: str):
        q = "SELECT ts, amount, price FROM snapshots WHERE currency=? AND ts>=? AND ts<? ORDER BY ts"
        rows = self.db.execute(q, (currency, start, end)).fetchall()
        before = self.db.execute(
            "SELECT ts, amount, price FROM snapshots WHERE currency=? AND ts<? ORDER BY ts DESC LIMIT 1",
            (currency, start)).fetchone()
        return before, rows

    def currencies(self) -> list[str]:
        return [r[0] for r in self.db.execute("SELECT DISTINCT currency FROM snapshots ORDER BY currency")]

    def month_report(self, month: str) -> list["ReportRow"]:
        """month = 'YYYY-MM'. Start = last snapshot before the month (or first in it)."""
        y, m = (int(x) for x in month.split("-"))
        start = f"{y:04d}-{m:02d}-01"
        end = f"{y + (m == 12):04d}-{(m % 12) + 1:02d}-01"
        out = []
        for cur in self.currencies():
            before, rows = self._first_last(cur, start, end)
            if not rows:
                continue
            first = before or rows[0]
            last = rows[-1]
            a0, a1 = Decimal(first[1]), Decimal(last[1])
            price = Decimal(last[2]) if last[2] is not None else None
            delta = a1 - a0
            out.append(ReportRow(cur, a0, a1, delta, price,
                                 None if price is None else (delta * price).quantize(Decimal("0.01")),
                                 None if price is None else (a1 * price).quantize(Decimal("0.01")),
                                 len(rows)))
        return out


@dataclass(frozen=True)
class ReportRow:
    currency: str
    start: Decimal
    end: Decimal
    change: Decimal
    price: Decimal | None
    change_fiat: Decimal | None
    end_fiat: Decimal | None
    snapshots: int
