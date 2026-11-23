"""Tiny .env reader (no dependency). Real environment variables always win."""
from __future__ import annotations

import os
from collections.abc import Iterable, MutableMapping
from pathlib import Path

HOME_DIR = Path.home() / ".faucetpay-ledger"


def read_env(path: str | Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"'):
            quote = value[0]
            value = value[1:].split(quote, 1)[0]
        else:
            value = value.split(" #", 1)[0].strip()
        out[key.strip()] = value
    return out


def load_env(paths: Iterable[str | Path] | None = None, environ: MutableMapping[str, str] | None = None):
    """Fill missing variables from ./.env, then from ~/.faucetpay-ledger/.env."""
    environ = os.environ if environ is None else environ
    for path in paths if paths is not None else (Path(".env"), HOME_DIR / ".env"):
        if not Path(path).is_file():
            continue
        for key, value in read_env(path).items():
            if value and not environ.get(key):
                environ[key] = value
