"""Command line: fpl snapshot | balances | report | payouts"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

from .api import FaucetPayClient, FaucetPayError
from .prices import fiat_prices
from .store import Store

DEFAULT_DB = os.environ.get("FPL_DB", str(Path.home() / ".faucetpay-ledger" / "ledger.db"))


def _client() -> FaucetPayClient:
    key = os.environ.get("FAUCETPAY_API_KEY", "")
    if not key:
        sys.exit("Set FAUCETPAY_API_KEY (see .env.example).")
    return FaucetPayClient(key)


def _fmt(x: Decimal | None, places: int = 8) -> str:
    return "-" if x is None else f"{x:.{places}f}"


def _balances(client: FaucetPayClient, currencies: list[str] | None):
    cur = currencies or client.currencies()
    out = []
    for c in cur:
        try:
            b = client.balance(c)
        except FaucetPayError as e:
            print(f"  skip {c}: {e}", file=sys.stderr)
            continue
        if b.amount != 0 or currencies:
            out.append(b)
    return out


def cmd_balances(a):
    bals = _balances(_client(), a.currencies)
    prices = fiat_prices([b.currency for b in bals], a.fiat)
    total = Decimal(0)
    for b in bals:
        v = prices.get(b.currency)
        val = None if v is None else b.amount * v
        total += val or 0
        print(f"{b.currency:<6} {_fmt(b.amount):>20}  {_fmt(val, 2):>10} {a.fiat.upper()}")
    print(f"{'TOTAL':<6} {'':>20}  {total:>10.2f} {a.fiat.upper()}")


def cmd_snapshot(a):
    store = Store(a.db)
    bals = _balances(_client(), a.currencies)
    prices = fiat_prices([b.currency for b in bals], a.fiat)
    for b in bals:
        store.add(b.currency, b.amount, a.fiat.lower(), prices.get(b.currency))
    print(f"Saved {len(bals)} balances to {store.path}")


def cmd_report(a):
    rows = Store(a.db).month_report(a.month)
    if not rows:
        print(f"No snapshots for {a.month}. Run `fpl snapshot` regularly (e.g. daily).")
        return
    hdr = ["currency", "start", "end", "change", "price", "change_fiat", "end_fiat", "snapshots"]
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(hdr)
            for r in rows:
                w.writerow([r.currency, r.start, r.end, r.change, r.price or "", r.change_fiat or "",
                            r.end_fiat or "", r.snapshots])
        print(f"Report written to {a.csv}")
    total = sum((r.change_fiat or 0) for r in rows)
    print(f"Report {a.month}")
    for r in rows:
        print(f"{r.currency:<6} {_fmt(r.start):>16} -> {_fmt(r.end):>16}  change {_fmt(r.change):>16}"
              f"  = {_fmt(r.change_fiat, 2):>8}")
    print(f"Total change: {total:.2f} (at month-end prices). Deposits/withdrawals are included - see README.")


def cmd_payouts(a):
    pays = _client().payouts(a.currency, a.count)
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["date", "to", "amount_smallest_unit", "currency"])
            for p in pays:
                w.writerow([p.date, p.to, p.amount, a.currency or ""])
        print(f"{len(pays)} payouts written to {a.csv}")
    else:
        for p in pays:
            print(f"{p.date}  {p.to:<40} {p.amount}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fpl", description="Read-only FaucetPay balance & earnings ledger.")
    p.add_argument("--db", default=DEFAULT_DB, help="SQLite file (default: %(default)s)")
    p.add_argument("--fiat", default="eur", help="fiat currency for values (default: eur)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cur(sp):
        sp.add_argument("--currencies", type=lambda s: [x.strip().upper() for x in s.split(",") if x.strip()],
                        help="comma list, e.g. BTC,LTC,DOGE (default: all non-zero)")

    s = sub.add_parser("balances", help="show current balances and their fiat value"); cur(s)
    s.set_defaults(func=cmd_balances)
    s = sub.add_parser("snapshot", help="save current balances to the ledger"); cur(s)
    s.set_defaults(func=cmd_snapshot)
    s = sub.add_parser("report", help="monthly change per coin")
    s.add_argument("--month", default=date.today().strftime("%Y-%m"), help="YYYY-MM (default: this month)")
    s.add_argument("--csv", help="also write a CSV file (';' separated, opens in Excel)")
    s.set_defaults(func=cmd_report)
    s = sub.add_parser("payouts", help="export payouts sent by your faucet (faucet owners)")
    s.add_argument("--currency"); s.add_argument("--count", type=int, default=100)
    s.add_argument("--csv")
    s.set_defaults(func=cmd_payouts)
    return p


def main(argv: list[str] | None = None):
    a = build_parser().parse_args(argv)
    try:
        a.func(a)
    except FaucetPayError as e:
        sys.exit(f"FaucetPay API error: {e}")


if __name__ == "__main__":
    main()
