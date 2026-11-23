"""Fiat prices from the public CoinGecko API (no key needed)."""
from __future__ import annotations

from decimal import Decimal

import requests

COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"

# FaucetPay symbol -> CoinGecko id. Unknown symbols simply get no fiat value.
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "LTC": "litecoin", "DOGE": "dogecoin",
    "BCH": "bitcoin-cash", "DASH": "dash", "DGB": "digibyte", "TRX": "tron",
    "USDT": "tether", "BNB": "binancecoin", "SOL": "solana", "XRP": "ripple",
    "ZEC": "zcash", "TON": "the-open-network", "ADA": "cardano", "XLM": "stellar",
    "XMR": "monero", "POL": "polygon-ecosystem-token", "MATIC": "matic-network",
    "PEPE": "pepe", "FEY": "feyorra",
}


def fiat_prices(symbols: list[str], fiat: str = "eur",
                session: requests.Session | None = None, timeout: float = 20.0) -> dict[str, Decimal]:
    """Return {SYMBOL: price_in_fiat} for the symbols CoinGecko knows."""
    fiat = fiat.lower()
    ids = {s.upper(): COINGECKO_IDS[s.upper()] for s in symbols if s.upper() in COINGECKO_IDS}
    if not ids:
        return {}
    s = session or requests.Session()
    resp = s.get(COINGECKO_URL, params={"ids": ",".join(sorted(set(ids.values()))), "vs_currencies": fiat},
                 timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    out = {}
    for sym, cg_id in ids.items():
        price = data.get(cg_id, {}).get(fiat)
        if price is not None:
            out[sym] = Decimal(str(price))
    return out
