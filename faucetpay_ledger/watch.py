"""Faucet balance checks: below a minimum, or running out soon."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from .api import Balance
from .store import Store

RATE_WINDOW = timedelta(hours=24)
REPEAT_AFTER = timedelta(hours=6)


@dataclass(frozen=True)
class Alert:
    key: str  # what must not be repeated too often, e.g. 'BTC:low'
    text: str


def hours_left(store: Store, balance: Balance, now: datetime) -> Decimal | None:
    """Hours until the balance is empty at the payout rate of the last 24 h, None if nothing was paid out.

    If only a part of the 24 h is known (see Store.complete_since), the rate is taken from that part.
    """
    start = now - RATE_WINDOW
    since = store.complete_since(balance.currency)
    if since is not None and since > start:
        start = since
    # API dates have whole seconds, so include the current one
    rows = store.payout_rows(start, now + timedelta(seconds=1), balance.currency)
    spent = sum((r[2] for r in rows), Decimal(0))
    if spent <= 0:
        return None
    hours = max(Decimal(str((now - start).total_seconds())) / 3600, Decimal(1) / 60)
    return balance.smallest / (spent / hours)


def check(store: Store, balance: Balance, minimum: Decimal, max_hours: Decimal, now: datetime) -> list[Alert]:
    cur, out = balance.currency, []
    if balance.amount < minimum:
        out.append(Alert(f"{cur}:low", f"{cur}: balance {balance.amount:f} is below {minimum:f}"))
    left = hours_left(store, balance, now)
    if left is not None and left < max_hours:
        out.append(Alert(f"{cur}:hours", f"{cur}: balance {balance.amount:f} lasts about {left:.1f} h "
                                         f"(limit {max_hours:f} h)"))
    return out
