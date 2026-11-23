# faucetpay-ledger

**Know what your FaucetPay balance is actually worth – every day, every month, in EUR.**

A tiny, **read-only** Python tool for [FaucetPay](https://faucetpay.io/?r=44490) users and faucet owners:

- 📸 `fpl snapshot` – saves the balances of all your coins (with EUR price) to a local SQLite file
- 💶 `fpl balances` – shows what every coin is worth right now, plus the total
- 📊 `fpl report --month 2026-11 --csv nov.csv` – monthly change per coin in coins **and** EUR,
  CSV opens directly in Excel / LibreOffice (handy for your tax return or benefit office)
- 📤 `fpl payouts --currency BTC --csv payouts.csv` – exports the last 100 payouts of your faucet

Safety first:

- It **never sends coins**. Only read endpoints of the API are used.
- Your data stays on your computer (one SQLite file). No telemetry.
- Prices come from the public CoinGecko API.

## Install

```bash
pip install git+https://github.com/PuchBuch/faucetpay-ledger
```

or clone and `pip install -e .` (Python 3.10+).

## Setup

1. Log in to FaucetPay → **Faucet Owner Dashboard** → create/open a faucet → copy its **API key**.
   Treat the key like a password: anyone with it can send coins from your account.
2. Set the key as an environment variable:

   ```powershell
   # Windows PowerShell (current session)
   $env:FAUCETPAY_API_KEY = "your-key"
   ```
   ```bash
   # Linux / macOS
   export FAUCETPAY_API_KEY="your-key"
   ```

## Usage

```bash
fpl balances                        # what do I have right now?
fpl snapshot                        # save today's balances (run once a day)
fpl report --month 2026-11          # what did I earn in November?
fpl report --month 2026-11 --csv november.csv
fpl --fiat usd balances             # values in USD instead of EUR
fpl payouts --currency LTC --csv ltc_payouts.csv   # faucet owners
```

### Run the snapshot daily (Windows)

Task Scheduler → *Create Basic Task* → daily → *Start a program*:
`powershell.exe -Command "$env:FAUCETPAY_API_KEY='your-key'; fpl snapshot"`

Linux cron: `0 8 * * * FAUCETPAY_API_KEY=your-key fpl snapshot`

## How the report works

For each coin the report takes the last snapshot **before** the month as the start
and the last snapshot **in** the month as the end. `change = end − start`, valued at the
month-end price. Deposits and withdrawals are part of the change – write them down
if you need pure earnings.

## Development

```bash
pip install -e ".[test]"
pytest
```

## Disclaimer

Not affiliated with FaucetPay. Crypto prices are volatile; this is not financial or tax advice.
The FaucetPay link above is my referral link – using it supports this project at no cost to you.

MIT License
