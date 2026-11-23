"""Send a Telegram message through the Bot API."""
from __future__ import annotations

import requests

API_URL = "https://api.telegram.org"


class TelegramError(RuntimeError):
    pass


def send_message(token: str, chat_id: str, text: str,
                 session: requests.Session | None = None, timeout: float = 20.0):
    s = session or requests.Session()
    try:
        resp = s.post(f"{API_URL}/bot{token}/sendMessage", data={"chat_id": chat_id, "text": text},
                      timeout=timeout)
        ok = resp.json().get("ok") is True
    except (requests.RequestException, ValueError, AttributeError) as e:
        # never include the exception text: it contains the URL, and the URL contains the token
        raise TelegramError(f"Telegram: network or response error ({type(e).__name__})") from None
    if not ok:
        raise TelegramError(f"Telegram refused the message (HTTP {resp.status_code}), check token and chat id")
