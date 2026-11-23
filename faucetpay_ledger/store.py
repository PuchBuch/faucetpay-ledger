"""SQLite storage: balance snapshots, synced payouts and the alert log."""
from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from .api import MAX_PAYOUTS, Balance, Payout
from .dates import DEFAULT_ORDER, detect_order, parse_date

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts        TEXT NOT NULL,      -- ISO-8601 UTC
    currency  TEXT NOT NULL,
    amount    TEXT NOT NULL,      -- Decimal as text, whole coins
    fiat      TEXT NOT NULL,      -- e.g. 'eur'
    price     TEXT                -- price of 1 coin in fiat, NULL if unknown
);
CREATE INDEX IF NOT EXISTS ix_snap ON snapshots(currency, ts);
CREATE TABLE IF NOT EXISTS payouts (
    currency  TEXT NOT NULL,
    recipient TEXT NOT NULL,
    amount    TEXT NOT NULL,      -- Decimal as text, smallest unit
    date      TEXT NOT NULL,      -- exactly as returned by the API
    seq       INTEGER NOT NULL,   -- 0, 1, ... for payouts that are equal in all fields above
    ts        TEXT,               -- date as ISO-8601 UTC, NULL if not understood
    PRIMARY KEY (currency, recipient, amount, date, seq)
);
CREATE INDEX IF NOT EXISTS ix_pay ON payouts(currency, ts);
CREATE TABLE IF NOT EXISTS coverage (
    currency  TEXT PRIMARY KEY,
    since     TEXT NOT NULL       -- payouts are complete from this API date on, '' = from the start
);
CREATE TABLE IF NOT EXISTS units (
    currency  TEXT PRIMARY KEY,
    decimals  INTEGER NOT NULL    -- smallest units per coin = 10^decimals
);
CREATE TABLE IF NOT EXISTS alerts (key TEXT PRIMARY KEY, sent_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# The docs name satoshis for BTC; other coins are learned from getbalance.
DOCUMENTED_DECIMALS = {"BTC": 8}


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True)
class SyncResult:
    new: int
    gap: bool  # the answer does not reach back to the last sync, payouts in between may be missing


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

    def month_report(self, month: str) -> list[ReportRow]:
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

    # --- payouts ------------------------------------------------------------

    def date_order(self) -> str | None:
        """Detected order of day and month in API dates, None while it is still ambiguous."""
        row = self.db.execute("SELECT value FROM meta WHERE key='date_order'").fetchone()
        return row[0] if row else None

    def parse(self, raw: str) -> datetime | None:
        return parse_date(raw, self.date_order() or DEFAULT_ORDER)

    def _learn_date_order(self, raws: list[str], now: datetime):
        if self.date_order():
            return
        order = detect_order(raws, now)
        if order is None:
            return
        self.db.execute("INSERT INTO meta VALUES ('date_order', ?)", (order,))
        for raw in [r[0] for r in self.db.execute("SELECT DISTINCT date FROM payouts")]:
            d = parse_date(raw, order)
            self.db.execute("UPDATE payouts SET ts=? WHERE date=?", (d and iso(d), raw))

    def add_payouts(self, currency: str, rows: list[Payout], now: datetime | None = None) -> SyncResult:
        """Store one API answer (newest first, up to MAX_PAYOUTS). Known payouts are skipped."""
        def key(p: Payout):
            return currency, p.to, f"{p.amount.normalize():f}", p.date  # 100 and 100.0 are equal

        with self.db:
            self._learn_date_order([p.date for p in rows], now or datetime.now(timezone.utc))
            had_any = self.db.execute("SELECT 1 FROM payouts WHERE currency=? LIMIT 1", (currency,)).fetchone()
            overlap = any(self.db.execute(
                "SELECT 1 FROM payouts WHERE currency=? AND recipient=? AND amount=? AND date=?",
                key(p)).fetchone() for p in rows)
            new, seen = 0, Counter()
            for p in rows:
                d = self.parse(p.date)
                cur = self.db.execute("INSERT OR IGNORE INTO payouts VALUES (?,?,?,?,?,?)",
                                      (*key(p), seen[key(p)], d and iso(d)))
                seen[key(p)] += 1
                new += cur.rowcount
            gap = bool(rows and had_any and not overlap)
            if not had_any:
                since = rows[-1].date if len(rows) >= MAX_PAYOUTS else ""
            elif gap:
                since = rows[-1].date
            else:
                since = None
            if since is not None:
                self.db.execute("INSERT OR REPLACE INTO coverage VALUES (?,?)", (currency, since))
        return SyncResult(new, gap)

    def complete_since(self, currency: str) -> datetime | None:
        """Payouts are complete from this moment on, None = from the very first payout."""
        row = self.db.execute("SELECT since FROM coverage WHERE currency=?", (currency,)).fetchone()
        return self.parse(row[0]) if row and row[0] else None

    def payout_rows(self, start: datetime, end: datetime, currency: str | None = None):
        """(currency, recipient, amount in smallest unit, ts) with start <= ts < end."""
        q = "SELECT currency, recipient, amount, ts FROM payouts WHERE ts>=? AND ts<?"
        args = [iso(start), iso(end)]
        if currency:
            q += " AND currency=?"
            args.append(currency)
        return [(c, to, Decimal(a), ts) for c, to, a, ts in self.db.execute(q + " ORDER BY currency, ts", args)]

    def undated_payouts(self) -> int:
        return self.db.execute("SELECT count(*) FROM payouts WHERE ts IS NULL").fetchone()[0]

    def has_payouts(self, currency: str | None = None) -> bool:
        return self.db.execute("SELECT 1 FROM payouts WHERE ? IS NULL OR currency=? LIMIT 1",
                               (currency, currency)).fetchone() is not None

    # --- units --------------------------------------------------------------

    def learn_decimals(self, b: Balance):
        """Both balances of getbalance together show how many smallest units make one coin."""
        if b.amount <= 0 or b.smallest <= 0:
            return
        ratio = b.smallest / b.amount
        decimals = round(ratio.log10())
        if abs(ratio / Decimal(10) ** decimals - 1) > Decimal("0.01"):
            return
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO units VALUES (?,?)", (b.currency, decimals))

    def decimals(self, currency: str) -> int | None:
        row = self.db.execute("SELECT decimals FROM units WHERE currency=?", (currency,)).fetchone()
        return row[0] if row else DOCUMENTED_DECIMALS.get(currency)

    # --- alerts -------------------------------------------------------------

    def alert_due(self, key: str, now: datetime, every: timedelta) -> bool:
        row = self.db.execute("SELECT sent_at FROM alerts WHERE key=?", (key,)).fetchone()
        return row is None or now - datetime.fromisoformat(row[0]) >= every

    def alert_sent(self, key: str, now: datetime):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO alerts VALUES (?,?)", (key, iso(now)))


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
