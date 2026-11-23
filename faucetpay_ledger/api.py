"""Minimal read-only client for the FaucetPay API v1.

Only endpoints that READ data are implemented on purpose:
this tool never sends coins.
Docs: https://faucetpay.io/api-docs
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import requests

BASE_URL = "https://faucetpay.io/api/v1"
MAX_PAYOUTS = 100  # documented maximum of `count`; there is no pagination

# v1 always answers HTTP 200, the real outcome is in the body's "status" field.
STATUS_HINTS = {
    401: "access denied: your IP is not whitelisted for this faucet, or the account is suspended",
    403: "invalid or missing API key",
    404: "unknown API method",
    410: "currency is not supported by FaucetPay",
    459: "rate limited: too many API requests, try again later",
}


class FaucetPayError(RuntimeError):
    """Raised when the API cannot be reached or answers with a non-200 status."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Balance:
    currency: str
    amount: Decimal  # in whole coins, e.g. 0.00049900 BTC
    smallest: Decimal = Decimal(0)  # the same balance in the smallest unit, e.g. 49900


@dataclass(frozen=True)
class Payout:
    to: str
    amount: Decimal  # smallest unit as returned by the API
    date: str


def _num(v, what: str) -> Decimal:
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        d = Decimal("NaN")
    if not d.is_finite():
        raise FaucetPayError(f"unexpected API response: {what} is not a number")
    return d


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
        try:
            resp = self.session.post(f"{self.base_url}/{endpoint}", data=data, timeout=self.timeout)
            resp.raise_for_status()
            payload = resp.json()
        except requests.Timeout:
            raise FaucetPayError(f"{endpoint}: no answer within {self.timeout:g} s") from None
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "error"
            raise FaucetPayError(f"{endpoint}: HTTP {code}") from None
        except (requests.RequestException, ValueError) as e:
            # only the type: the text of a low-level error could contain request details
            raise FaucetPayError(f"{endpoint}: network or response error ({type(e).__name__})") from None
        if not isinstance(payload, dict):
            raise FaucetPayError(f"{endpoint}: unexpected API response")
        try:
            status = int(payload.get("status", 0))
        except (TypeError, ValueError):
            status = 0
        if status != 200:
            text = STATUS_HINTS.get(status) or str(payload.get("message")).replace(self.api_key, "***")
            raise FaucetPayError(f"{endpoint}: {status} {text}", status)
        return payload

    def currencies(self) -> list[str]:
        return [c.upper() for c in self._post("currencies").get("currencies", [])]

    def balance(self, currency: str) -> Balance:
        p = self._post("getbalance", currency=currency.upper())
        # "balance_bitcoin" is the balance in whole coins of the requested currency
        return Balance(currency.upper(), _num(p.get("balance_bitcoin"), "balance_bitcoin"),
                       _num(p.get("balance"), "balance"))

    def payouts(self, currency: str | None = None, count: int = MAX_PAYOUTS) -> list[Payout]:
        """Most recent payouts, newest first. Items carry no currency, so filter by one to know it."""
        count = max(1, min(int(count), MAX_PAYOUTS))
        p = self._post("payouts", currency=currency.upper() if currency else None, count=count)
        rows = p.get("rewards", [])
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise FaucetPayError("payouts: unexpected API response")
        return [Payout(str(r.get("to", "")), _num(r.get("amount"), "amount"), str(r.get("date", "")))
                for r in rows]
