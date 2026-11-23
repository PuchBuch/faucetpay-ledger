"""Payout sync, stats, watch, Telegram and .env. Fully offline."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
import requests

from faucetpay_ledger import cli
from faucetpay_ledger.api import Balance, FaucetPayClient, FaucetPayError, Payout
from faucetpay_ledger.dates import detect_order, parse_date
from faucetpay_ledger.env import load_env, read_env
from faucetpay_ledger.stats import payout_stats
from faucetpay_ledger.store import Store
from faucetpay_ledger.telegram import TelegramError, send_message
from faucetpay_ledger.watch import REPEAT_AFTER, check, hours_left

NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)


def pay(to="a@b.c", amount=100, date="23-11-26 10:00:00 GMT"):
    return Payout(to, Decimal(str(amount)), date)


def at(d: datetime) -> str:
    return d.strftime("%d-%m-%y %H:%M:%S GMT")


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "l.db")


# --- dates -------------------------------------------------------------------

def test_date_order_is_detected_from_data():
    assert detect_order(["23-11-26 10:00:00 GMT"], NOW) == "dmy"
    assert detect_order(["11-23-26 10:00:00 GMT"], NOW) == "mdy"
    assert detect_order(["11-12-26 10:00:00 GMT"], NOW) == "mdy"  # 11 December would be in the future
    assert detect_order(["02-05-26 21:12:00 GMT"], NOW) is None  # both are possible
    assert detect_order(["23-11-26 10:00:00", "11-23-26 10:00:00"], NOW) is None  # contradiction
    assert detect_order([], NOW) is None


def test_parse_date():
    assert parse_date("02-05-26 21:12:00 GMT", "dmy") == datetime(2026, 5, 2, 21, 12, tzinfo=timezone.utc)
    assert parse_date("02-05-26 21:12:00 GMT", "mdy") == datetime(2026, 2, 5, 21, 12, tzinfo=timezone.utc)
    assert parse_date("2026-11-01 10:00:00") == datetime(2026, 11, 1, 10, tzinfo=timezone.utc)
    assert parse_date("yesterday") is None


def test_ambiguous_dates_are_reparsed_once_the_order_is_known(store):
    store.add_payouts("BTC", [pay(date="05-06-26 10:00:00 GMT")], NOW)
    assert store.date_order() is None
    store.add_payouts("LTC", [pay(date="11-23-26 10:00:00 GMT")], NOW)
    assert store.date_order() == "mdy"
    (row,) = store.payout_rows(datetime(2026, 1, 1, tzinfo=timezone.utc), NOW, "BTC")
    assert row[3].startswith("2026-05-06")


# --- sync --------------------------------------------------------------------

def test_sync_has_no_duplicates(store):
    rows = [pay("b@b.c", 200, "23-11-26 11:00:00 GMT"), pay()]
    assert store.add_payouts("BTC", rows, NOW).new == 2
    assert store.add_payouts("BTC", rows, NOW).new == 0
    assert store.add_payouts("BTC", [pay("c@b.c", 5, "23-11-26 11:30:00 GMT"), *rows], NOW).new == 1
    assert store.add_payouts("BTC", [pay(amount="100.0")], NOW).new == 0
    assert store.add_payouts("LTC", [pay()], NOW).new == 1  # another coin is another payout


def test_sync_keeps_identical_payouts_of_the_same_second(store):
    assert store.add_payouts("BTC", [pay(), pay()], NOW).new == 2
    assert store.add_payouts("BTC", [pay(), pay(), pay()], NOW).new == 1
    assert store.add_payouts("BTC", [pay()], NOW).new == 0


def batch(newest: datetime, n=100):
    return [pay("u", 10, at(newest - timedelta(seconds=i))) for i in range(n)]


def test_sync_reports_gaps(store):
    old = NOW - timedelta(hours=10)
    assert store.add_payouts("BTC", batch(old), NOW).gap is False
    assert store.complete_since("BTC") == old - timedelta(seconds=99)  # full answer: there may be older ones
    assert store.add_payouts("BTC", batch(old + timedelta(seconds=50)), NOW).gap is False  # overlaps
    assert store.complete_since("BTC") == old - timedelta(seconds=99)
    assert store.add_payouts("BTC", batch(NOW), NOW).gap is True
    assert store.complete_since("BTC") == NOW - timedelta(seconds=99)


def test_short_first_answer_means_complete_history(store):
    store.add_payouts("BTC", [pay()], NOW)
    assert store.complete_since("BTC") is None
    assert store.add_payouts("BTC", [], NOW).gap is False


# --- stats -------------------------------------------------------------------

def fill(store):
    store.add_payouts("BTC", [
        pay("a", 300, "23-11-26 09:00:00 GMT"),
        pay("b", 100, "22-11-26 09:00:00 GMT"),
        pay("a", 200, "22-11-26 08:00:00 GMT"),
        pay("z", 999, "31-10-26 23:59:59 GMT"),
    ], NOW)
    store.add_payouts("XYZ", [pay("a", 7, "23-11-26 09:00:00 GMT")], NOW)


def test_stats(store):
    fill(store)
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    btc, xyz = payout_stats(store, start, NOW, {"BTC": Decimal(80000)})
    t = btc.total
    assert (t.payouts, t.recipients, t.smallest) == (3, 2, 600)
    assert t.coins == Decimal("0.000006") and t.fiat == Decimal("0.48")
    assert t.avg_coins == Decimal("0.000002") and t.avg_fiat == Decimal("0.16")
    assert [(d, x.payouts, x.recipients, x.smallest) for d, x in btc.days.items()] == \
        [("2026-11-22", 2, 2, 300), ("2026-11-23", 1, 1, 300)]
    # unit of XYZ is not known: nothing is guessed
    assert xyz.total.smallest == 7 and xyz.total.coins is None and xyz.total.fiat is None


def test_decimals_are_learned_from_balance(store):
    assert store.decimals("BTC") == 8 and store.decimals("XYZ") is None
    store.learn_decimals(Balance("XYZ", Decimal(0), Decimal(0)))
    assert store.decimals("XYZ") is None
    store.learn_decimals(Balance("XYZ", Decimal("1.23"), Decimal(1230000)))
    assert store.decimals("XYZ") == 6
    store.learn_decimals(Balance("ODD", Decimal(1), Decimal(3)))  # not a power of ten
    assert store.decimals("ODD") is None


# --- watch -------------------------------------------------------------------

def test_hours_left_uses_last_24h(store):
    store.add_payouts("BTC", [pay("a", 1200, at(NOW - timedelta(hours=1))),
                              pay("b", 1200, at(NOW - timedelta(hours=20))),
                              pay("c", 99999, at(NOW - timedelta(hours=30)))], NOW)
    b = Balance("BTC", Decimal("0.00001"), Decimal(1000))
    assert hours_left(store, b, NOW) == 10  # 2400 per 24 h = 100 per hour


def test_hours_left_uses_the_known_part_of_the_day(store):
    # 100 payouts x 10 within 99 s and nothing known before: the rate must not be spread over 24 h
    store.add_payouts("BTC", batch(NOW), NOW)
    assert hours_left(store, Balance("BTC", Decimal(1), Decimal(1000)), NOW) < 1


def test_hours_left_without_payouts(store):
    assert hours_left(store, Balance("BTC", Decimal(1), Decimal(10**8)), NOW) is None


def test_check(store):
    store.add_payouts("BTC", [pay("a", 2400, at(NOW - timedelta(hours=1)))], NOW)

    def keys(smallest, minimum, hours):
        b = Balance("BTC", Decimal(smallest) / 10**8, Decimal(smallest))
        return [x.key for x in check(store, b, Decimal(minimum), Decimal(hours), NOW)]

    assert keys(1000, "0.1", 24) == ["BTC:low", "BTC:hours"]
    assert keys(1000, "0.00001", 10) == []  # exactly at both limits
    assert keys(1000, "0", 24) == ["BTC:hours"]
    assert keys(1000, "0.1", 0) == ["BTC:low"]  # 0 switches the hours check off


def test_alert_is_not_repeated_within_6_hours(store):
    assert store.alert_due("BTC:low", NOW, REPEAT_AFTER)
    store.alert_sent("BTC:low", NOW)
    assert not store.alert_due("BTC:low", NOW + timedelta(hours=5, minutes=59), REPEAT_AFTER)
    assert store.alert_due("BTC:low", NOW + timedelta(hours=6), REPEAT_AFTER)
    assert store.alert_due("LTC:low", NOW, REPEAT_AFTER)


# --- Telegram ----------------------------------------------------------------

class TgResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload


class TgSession:
    def __init__(self, result):
        self.result, self.calls = result, []

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_telegram_send():
    s = TgSession(TgResp({"ok": True}))
    send_message("T0KEN", "42", "hi", session=s)
    assert s.calls == [("https://api.telegram.org/botT0KEN/sendMessage", {"chat_id": "42", "text": "hi"})]


@pytest.mark.parametrize("result", [
    requests.ConnectionError("failed: https://api.telegram.org/botT0KEN/sendMessage"),
    TgResp({"ok": False, "description": "Unauthorized"}, 401),
    TgResp(["?"]),
], ids=["network", "refused", "not-dict"])
def test_telegram_errors_do_not_leak_the_token(result):
    with pytest.raises(TelegramError) as e:
        send_message("T0KEN", "42", "hi", session=TgSession(result))
    assert "T0KEN" not in str(e.value)


# --- API errors --------------------------------------------------------------

class ApiSession:
    def __init__(self, result):
        self.result = result

    def post(self, url, data=None, timeout=None):
        if isinstance(self.result, Exception):
            raise self.result
        r = requests.Response()
        r.status_code, r._content = self.result
        return r


@pytest.mark.parametrize("result, text", [
    ((200, b'{"status": 403, "message": "x"}'), "invalid or missing API key"),
    ((200, b'{"status": 459, "message": "x"}'), "rate limited"),
    ((200, b'{"status": 999, "message": "bad S3CRET"}'), "999 bad \\*\\*\\*"),
    ((502, b""), "HTTP 502"),
    ((200, b"<html>"), "network or response error"),
    ((200, b"[]"), "unexpected API response"),
    (requests.Timeout("S3CRET"), "no answer within"),
    (requests.ConnectionError("S3CRET"), "ConnectionError"),
])
def test_api_errors_are_clear_and_do_not_leak_the_key(result, text):
    with pytest.raises(FaucetPayError, match=text) as e:
        FaucetPayClient("S3CRET", session=ApiSession(result)).balance("BTC")
    assert "S3CRET" not in str(e.value)


def test_missing_balance_is_an_error_not_zero():
    s = ApiSession((200, b'{"status": 200, "balance": 5}'))
    with pytest.raises(FaucetPayError, match="balance_bitcoin"):
        FaucetPayClient("k", session=s).balance("BTC")


def test_client_is_read_only():
    public = {n for n in vars(FaucetPayClient) if not n.startswith("_")}
    assert public == {"currencies", "balance", "payouts"}


# --- .env --------------------------------------------------------------------

def test_read_env(tmp_path):
    f = tmp_path / ".env"
    f.write_bytes(b'\xef\xbb\xbf# comment\r\nA=1\r\nexport B = "x # y" # note\r\nC=\'q\'\r\nD=v # note\r\n'
                  b'E=a=b#c\r\nbroken\r\nF=\r\n')
    assert read_env(f) == {"A": "1", "B": "x # y", "C": "q", "D": "v", "E": "a=b#c", "F": ""}


def test_load_env_does_not_override(tmp_path):
    first, second = tmp_path / "1", tmp_path / "2"
    first.write_text("A=first\nB=first\n")
    second.write_text("B=second\nC=second\n")
    env = {"A": "real"}
    load_env([first, second, tmp_path / "missing"], env)
    assert env == {"A": "real", "B": "first", "C": "second"}


# --- command line ------------------------------------------------------------

class FakeClient:
    def __init__(self):
        self.smallest = 1000
        self.rows = [pay("a", 2400, at(datetime.now(timezone.utc) - timedelta(hours=1)))]

    def currencies(self):
        return ["BTC", "LTC"]

    def balance(self, currency):
        return Balance(currency, Decimal(self.smallest) / 10**8, Decimal(self.smallest))

    def payouts(self, currency=None, count=100):
        return self.rows if currency == "BTC" else []


@pytest.fixture
def run(tmp_path, monkeypatch):
    fake = FakeClient()
    sent = []
    monkeypatch.setattr(cli, "load_env", lambda: None)
    monkeypatch.setattr(cli, "_client", lambda: fake)
    monkeypatch.setattr(cli, "fiat_prices", lambda symbols, fiat: {"BTC": Decimal(80000)})
    monkeypatch.setattr(cli, "send_message", lambda *a: sent.append(a))
    for name in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "FPL_DB"):
        monkeypatch.delenv(name, raising=False)

    def _run(*args):
        return cli.main(["--db", str(tmp_path / "cli.db"), *args])

    _run.client, _run.sent = fake, sent
    return _run


def test_cli_sync_and_stats(run, capsys, tmp_path):
    assert run("stats") == 0 and "payouts sync" in capsys.readouterr().out
    assert run("payouts", "sync") == 0 and "1 new payouts saved" in capsys.readouterr().out
    assert run("payouts", "sync", "--currency", "btc") == 0 and "0 new payouts" in capsys.readouterr().out
    out = tmp_path / "s.csv"
    assert run("stats", "--days", "2", "--csv", str(out)) == 0
    text = capsys.readouterr().out
    assert "0.00002400" in text and "BTC by day" in text
    rows = list(csv.DictReader(out.open(encoding="utf-8"), delimiter=";"))
    assert [r["day"] for r in rows][-1] == "TOTAL" and rows[-1]["amount"] == "0.000024"
    assert Decimal(rows[-1]["amount_eur"]) == Decimal("1.92")


def test_cli_stats_without_prices(run, capsys, monkeypatch):
    def down(*_):
        raise requests.ConnectionError

    monkeypatch.setattr(cli, "fiat_prices", down)
    run("payouts", "sync")
    assert run("stats") == 0
    res = capsys.readouterr()
    assert "0.00002400" in res.out and "CoinGecko is not available" in res.err


def test_cli_watch_without_telegram_returns_2(run, capsys):
    assert run("watch", "--min", "btc=0.1") == 2
    out = capsys.readouterr().out
    assert "below 0.1" in out and "lasts about" in out
    run.client.smallest = 10**10
    assert run("watch", "--min", "BTC=0.1") == 0
    assert run.sent == []


def test_cli_watch_telegram_is_not_repeated(run, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    assert run("watch", "--min", "BTC=0.1") == 0
    assert run("watch", "--min", "BTC=0.1") == 0
    assert len(run.sent) == 1 and run.sent[0][:2] == ("t", "42")


def test_cli_watch_failed_telegram_is_retried(run, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    working = cli.send_message

    def down(*_):
        raise TelegramError("down")

    monkeypatch.setattr(cli, "send_message", down)
    with pytest.raises(SystemExit, match="down"):
        run("watch", "--min", "BTC=0.1")
    monkeypatch.setattr(cli, "send_message", working)
    run("watch", "--min", "BTC=0.1")
    assert len(run.sent) == 1


def test_cli_watch_needs_both_telegram_settings(run, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    with pytest.raises(SystemExit, match="both"):
        run("watch", "--min", "BTC=0.1")


@pytest.mark.parametrize("args", [
    ["watch"], ["watch", "--min", "BTC"], ["watch", "--min", "BTC=x"], ["watch", "--min", "BTC=-1"],
    ["watch", "--min", "BTC=NaN"], ["stats", "--month", "2026-13"], ["stats", "--days", "0"],
    ["stats", "--days", "5", "--month", "2026-11"],
])
def test_cli_rejects_bad_arguments(run, args):
    with pytest.raises(SystemExit) as e:
        run(*args)
    assert e.value.code == 2
