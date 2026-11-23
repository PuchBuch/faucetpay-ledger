"""Minimal read-only client for the FaucetPay API v1.

Only endpoints that READ data are implemented on purpose:
this tool never sends coins.
Docs: https://faucetpay.io/api-docs
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import requests

BASE_URL = "https://faucetpay.io/api/v1"


class FaucetPayError(RuntimeError):
    """Raised when the API answers with a non-200 status."""


@dataclass(frozen=True)
class Balance:
    currency: str
    amount: Decimal  # in whole coins, e.g. 0.00049900 BTC


@dataclass(frozen=True)
class Payout:
    to: str
    amount: Decimal  # smallest unit as returned by the API
    date: str


class FaucetPayClient:
    def __init__(self, api_key: str, session: requests.Session | None = None,
                 base_url: str = BASE_URL, timeout: float = 20.0):
        if not api_key:
            raise ValueError("FAUCETPAY_API_KEY is empty")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, endpoint: str, **fields) -> dict:
        data = {"api_key": self.api_key, **{k: v for k, v in fields.items() if v is not None}}
        resp = self.session.post(f"{self.base_url}/{endpoint}", data=data, timeout=self.timeout)
        resp.raise_for_status()
        payload = resp.json()
        if int(payload.get("status", 0)) != 200:
            raise FaucetPayError(f"{endpoint}: {payload.get('status')} {payload.get('message')}")
        return payload

    def currencies(self) -> list[str]:
        return [c.upper() for c in self._post("currencies").get("currencies", [])]

    def balance(self, currency: str) -> Balance:
        p = self._post("getbalance", currency=currency.upper())
        # "balance_bitcoin" is the balance in whole coins of the requested currency
        return Balance(currency.upper(), Decimal(str(p.get("balance_bitcoin", "0"))))

    def payouts(self, currency: str | None = None, count: int = 100) -> list[Payout]:
        count = max(1, min(int(count), 100))
        p = self._post("payouts", currency=currency.upper() if currency else None, count=count)
        return [Payout(str(r.get("to", "")), Decimal(str(r.get("amount", "0"))), str(r.get("date", "")))
                for r in p.get("rewards", [])]
