from decimal import Decimal

import pytest

from faucetpay_ledger.api import FaucetPayClient, FaucetPayError
from faucetpay_ledger.prices import fiat_prices
from faucetpay_ledger.store import Store


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def post(self, url, data=None, timeout=None):
        self.calls.append((url, data))
        return FakeResp(self.routes[url.rsplit("/", 1)[1]](data))

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return FakeResp(self.routes["price"](params))


def test_balance_and_currencies():
    s = FakeSession({
        "currencies": lambda d: {"status": 200, "currencies": ["btc", "LTC"]},
        "getbalance": lambda d: {"status": 200, "currency": d["currency"], "balance": 49900,
                                 "balance_bitcoin": 0.000499},
    })
    c = FaucetPayClient("k", session=s)
    assert c.currencies() == ["BTC", "LTC"]
    b = c.balance("btc")
    assert b.currency == "BTC" and b.amount == Decimal("0.000499")
    assert s.calls[-1][1] == {"api_key": "k", "currency": "BTC"}


def test_error_status_raises():
    s = FakeSession({"getbalance": lambda d: {"status": 401, "message": "Invalid API key"}})
    with pytest.raises(FaucetPayError, match="401"):
        FaucetPayClient("bad", session=s).balance("BTC")


def test_payouts_count_is_clamped():
    s = FakeSession({"payouts": lambda d: {"status": 200, "rewards": [
        {"to": "a@b.c", "amount": 10, "date": "2026-11-01 10:00:00"}]}})
    p = FaucetPayClient("k", session=s).payouts("btc", count=500)
    assert s.calls[-1][1]["count"] == 100 and p[0].amount == Decimal(10)


def test_prices_maps_known_symbols_only():
    s = FakeSession({"price": lambda p: {"bitcoin": {"eur": 80000}}})
    assert fiat_prices(["BTC", "XYZ"], session=s) == {"BTC": Decimal("80000")}
    assert fiat_prices(["XYZ"], session=s) == {}


def test_month_report(tmp_path):
    st = Store(tmp_path / "l.db")
    st.add("BTC", Decimal("0.0001"), "eur", Decimal("80000"), ts="2026-10-31T23:00:00+00:00")
    st.add("BTC", Decimal("0.00012"), "eur", Decimal("80000"), ts="2026-11-10T08:00:00+00:00")
    st.add("BTC", Decimal("0.00015"), "eur", Decimal("90000"), ts="2026-11-30T08:00:00+00:00")
    st.add("BTC", Decimal("0.00020"), "eur", Decimal("90000"), ts="2026-12-01T08:00:00+00:00")
    (r,) = st.month_report("2026-11")
    assert r.start == Decimal("0.0001") and r.end == Decimal("0.00015")
    assert r.change == Decimal("0.00005") and r.change_fiat == Decimal("4.50")
    assert r.snapshots == 2
    assert st.month_report("2026-12")[0].start == Decimal("0.00015")


def test_empty_key_rejected():
    with pytest.raises(ValueError):
        FaucetPayClient("")
