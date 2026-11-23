"""Payout statistics per coin and per day."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .store import Store


@dataclass(frozen=True)
class Totals:
    """`coins` is None if the coin's smallest unit is unknown, `fiat` also if its price is."""

    payouts: int
    recipients: int
    smallest: Decimal
    coins: Decimal | None
    fiat: Decimal | None

    @property
    def avg_coins(self) -> Decimal | None:
        return None if self.coins is None else self.coins / self.payouts

    @property
    def avg_fiat(self) -> Decimal | None:
        return None if self.fiat is None else self.fiat / self.payouts


@dataclass(frozen=True)
class CoinStats:
    currency: str
    total: Totals
    days: dict[str, Totals] = field(default_factory=dict)  # 'YYYY-MM-DD' (UTC) -> totals


def _totals(rows, decimals: int | None, price: Decimal | None) -> Totals:
    smallest = sum((r[2] for r in rows), Decimal(0))
    coins = None if decimals is None else smallest / Decimal(10) ** decimals
    fiat = None if coins is None or price is None else coins * price
    return Totals(len(rows), len({r[1] for r in rows}), smallest, coins, fiat)


def payout_stats(store: Store, start: datetime, end: datetime, prices: dict[str, Decimal]) -> list[CoinStats]:
    by_coin = defaultdict(list)
    for r in store.payout_rows(start, end):
        by_coin[r[0]].append(r)
    out = []
    for cur, rows in sorted(by_coin.items()):
        dec, price = store.decimals(cur), prices.get(cur)
        by_day = defaultdict(list)
        for r in rows:
            by_day[r[3][:10]].append(r)
        out.append(CoinStats(cur, _totals(rows, dec, price),
                             {d: _totals(x, dec, price) for d, x in sorted(by_day.items())}))
    return out
