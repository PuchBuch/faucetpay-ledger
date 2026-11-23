"""Command line: fpl balances | snapshot | report | payouts [sync] | stats | watch"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import requests

from .api import MAX_PAYOUTS, FaucetPayClient, FaucetPayError
from .env import HOME_DIR, load_env
from .prices import fiat_prices
from .stats import payout_stats
from .store import Store
from .telegram import TelegramError, send_message
from .watch import REPEAT_AFTER, check


def _client() -> FaucetPayClient:
    key = os.environ.get("FAUCETPAY_API_KEY", "")
    if not key:
        sys.exit("Set FAUCETPAY_API_KEY (see .env.example).")
    return FaucetPayClient(key)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(x: Decimal | None, places: int = 8) -> str:
    return "-" if x is None else f"{x:.{places}f}"


def _prices(symbols: list[str], fiat: str) -> dict[str, Decimal]:
    try:
        return fiat_prices(symbols, fiat)
    except requests.RequestException as e:
        print(f"  no prices: CoinGecko is not available ({type(e).__name__})", file=sys.stderr)
        return {}


def _balances(client: FaucetPayClient, currencies: list[str] | None):
    cur = currencies or client.currencies()
    out = []
    for c in cur:
        try:
            b = client.balance(c)
        except FaucetPayError as e:
            if e.status != 410:  # only an unsupported coin is skipped
                raise
            print(f"  skip {c}: {e}", file=sys.stderr)
            continue
        if b.amount != 0 or currencies:
            out.append(b)
    return out


def cmd_balances(a):
    bals = _balances(_client(), a.currencies)
    prices = _prices([b.currency for b in bals], a.fiat)
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
    prices = _prices([b.currency for b in bals], a.fiat)
    for b in bals:
        store.add(b.currency, b.amount, a.fiat.lower(), prices.get(b.currency))
        store.learn_decimals(b)
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


def _sync(client: FaucetPayClient, store: Store, currency: str) -> int:
    rows = client.payouts(currency, MAX_PAYOUTS)
    res = store.add_payouts(currency, rows)
    if res.gap:
        print(f"  {currency}: payouts since the last sync may be missing (the API returns only the latest "
              f"{MAX_PAYOUTS}). Sync more often.", file=sys.stderr)
    return res.new


def cmd_payouts(a):
    if a.action == "sync":
        client, store = _client(), Store(a.db)
        total = 0
        for c in [a.currency] if a.currency else client.currencies():
            new = _sync(client, store, c)
            total += new
            if new:
                print(f"{c:<6} {new} new payouts")
            if store.decimals(c) is None and store.has_payouts(c):
                store.learn_decimals(client.balance(c))
        print(f"{total} new payouts saved to {store.path}")
        return
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


def _period(a) -> tuple[datetime, datetime, str]:
    if a.month:
        y, m = (int(x) for x in a.month.split("-"))
        return (datetime(y, m, 1, tzinfo=timezone.utc),
                datetime(y + (m == 12), m % 12 + 1, 1, tzinfo=timezone.utc), a.month)
    now = _now()
    return now - timedelta(days=a.days), now + timedelta(seconds=1), f"last {a.days} days"


def _stats_csv(path: str, stats, fiat: str):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["currency", "day", "payouts", "recipients", "amount", f"amount_{fiat}",
                    "avg_amount", f"avg_{fiat}"])
        for s in stats:
            for day, t in [*s.days.items(), ("TOTAL", s.total)]:
                w.writerow([s.currency, day, t.payouts, t.recipients,
                            *("" if x is None else f"{x:f}" for x in (t.coins, t.fiat, t.avg_coins, t.avg_fiat))])


def cmd_stats(a):
    store = Store(a.db)
    start, end, title = _period(a)
    stats = payout_stats(store, start, end, {})
    if not stats:
        print(f"No payouts stored for {title}."
              + ("" if store.has_payouts() else " Run `fpl payouts sync` first."))
        return
    stats = payout_stats(store, start, end, _prices([s.currency for s in stats], a.fiat))
    if a.csv:
        _stats_csv(a.csv, stats, a.fiat.lower())
        print(f"Stats written to {a.csv}")
    fiat = a.fiat.upper()
    head = f"{'payouts':>8} {'recipients':>10} {'amount':>18} {fiat:>10} {'avg amount':>14} {'avg ' + fiat:>10}"

    def line(label, t):
        print(f"{label:<10} {t.payouts:>8} {t.recipients:>10} {_fmt(t.coins):>18} {_fmt(t.fiat, 2):>10} "
              f"{_fmt(t.avg_coins):>14} {_fmt(t.avg_fiat, 6):>10}")

    print(f"Payouts, {title} ({fiat} at current prices)")
    print(f"{'coin':<10} {head}")
    for s in stats:
        line(s.currency, s.total)
    for s in stats:
        print(f"\n{s.currency} by day (UTC)\n{'day':<10} {head}")
        for day, t in s.days.items():
            line(day, t)

    for s in stats:
        since = store.complete_since(s.currency)
        if since is not None and since > start:
            print(f"Note: {s.currency} payouts before {since:%Y-%m-%d %H:%M} UTC may be incomplete.")
        if s.total.coins is None:
            print(f"Note: {s.currency} unit is unknown, total is {s.total.smallest:f} smallest units. "
                  "Sync again when the balance is not zero.")
    _date_notes(store)


def _date_notes(store: Store):
    if store.has_payouts() and store.date_order() is None:
        print("Note: day/month order of the API dates is not certain yet, assuming day first.")
    if store.undated_payouts():
        print(f"Note: {store.undated_payouts()} payouts with an unknown date format are not counted.")


def cmd_watch(a):
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if bool(token) != bool(chat):
        sys.exit("Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or none of them.")
    client, store = _client(), Store(a.db)
    alerts = []
    for cur, minimum in a.min.items():
        _sync(client, store, cur)
        b = client.balance(cur)
        store.learn_decimals(b)
        alerts += check(store, b, minimum, a.hours_left, _now())
    _date_notes(store)
    if not alerts:
        print("OK: all balances are fine.")
        return 0
    for x in alerts:
        print(f"WARNING {x.text}")
    if not token:
        return 2
    now = _now()
    due = [x for x in alerts if store.alert_due(x.key, now, REPEAT_AFTER)]
    if due:
        send_message(token, chat, "FaucetPay faucet warning\n" + "\n".join(x.text for x in due))
        for x in due:
            store.alert_sent(x.key, now)
    print(f"Telegram: {len(due)} sent, {len(alerts) - len(due)} already sent in the last 6 h")
    return 0


def _month(s: str) -> str:
    try:
        return datetime.strptime(s, "%Y-%m").strftime("%Y-%m")
    except ValueError:
        raise argparse.ArgumentTypeError("expected YYYY-MM") from None


def _number(s: str) -> Decimal:
    try:
        d = Decimal(s)
    except InvalidOperation:
        d = Decimal("NaN")
    if not d.is_finite() or d < 0:
        raise argparse.ArgumentTypeError(f"not a number >= 0: {s!r}")
    return d


def _days(s: str) -> int:
    if not s.isdecimal() or not 1 <= int(s) <= 36500:
        raise argparse.ArgumentTypeError("expected a number of days from 1 to 36500")
    return int(s)


def _minimums(s: str) -> dict[str, Decimal]:
    out = {}
    for part in s.split(","):
        coin, sep, value = part.partition("=")
        if not sep or not coin.strip():
            raise argparse.ArgumentTypeError("expected COIN=AMOUNT,COIN=AMOUNT")
        out[coin.strip().upper()] = _number(value.strip())
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="fpl", description="Read-only FaucetPay ledger and monitor for faucet owners.")
    p.add_argument("--db", default=os.environ.get("FPL_DB") or str(HOME_DIR / "ledger.db"),
                   help="SQLite file (default: %(default)s)")
    p.add_argument("--fiat", default="eur", help="fiat currency for values (default: eur)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cur(sp):
        sp.add_argument("--currencies", type=lambda s: [x.strip().upper() for x in s.split(",") if x.strip()],
                        help="comma list, e.g. BTC,LTC,DOGE (default: all non-zero)")

    s = sub.add_parser("balances", help="show current balances and their fiat value")
    cur(s)
    s.set_defaults(func=cmd_balances)
    s = sub.add_parser("snapshot", help="save current balances to the ledger")
    cur(s)
    s.set_defaults(func=cmd_snapshot)
    s = sub.add_parser("report", help="monthly change per coin")
    s.add_argument("--month", type=_month, default=date.today().strftime("%Y-%m"),
                   help="YYYY-MM (default: this month)")
    s.add_argument("--csv", help="also write a CSV file (';' separated, opens in Excel)")
    s.set_defaults(func=cmd_report)
    s = sub.add_parser("payouts", help="show the latest payouts, or save new ones with `payouts sync`")
    s.add_argument("action", nargs="?", choices=["sync"], help="sync: save new payouts to the ledger")
    s.add_argument("--currency", type=str.upper, help="one coin (default: all)")
    s.add_argument("--count", type=int, default=MAX_PAYOUTS, help="without sync: how many to show")
    s.add_argument("--csv", help="without sync: write a CSV file")
    s.set_defaults(func=cmd_payouts)
    s = sub.add_parser("stats", help="payout statistics from the ledger")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--days", type=_days, default=30, help="last N days (default: 30)")
    g.add_argument("--month", type=_month, help="YYYY-MM")
    s.add_argument("--csv", help="also write a CSV file (';' separated)")
    s.set_defaults(func=cmd_stats)
    s = sub.add_parser("watch", help="warn when a faucet balance is low (exit code 2, or Telegram)")
    s.add_argument("--min", type=_minimums, required=True, help="e.g. BTC=0.0005,LTC=0.1")
    s.add_argument("--hours-left", type=_number, default=Decimal(24),
                   help="warn if the balance lasts less than N hours (default: 24, 0 = off)")
    s.set_defaults(func=cmd_watch)
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        load_env()
    except UnicodeDecodeError:
        sys.exit("Cannot read .env: save it as UTF-8.")
    a = build_parser().parse_args(argv)
    try:
        return a.func(a) or 0
    except FaucetPayError as e:
        sys.exit(f"FaucetPay API error: {e}")
    except TelegramError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    sys.exit(main())
