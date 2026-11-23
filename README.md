# faucetpay-ledger

**Know what your faucet pays out, and get a warning before it runs dry.**

A small, **read-only** command line tool for owners of faucets on
[FaucetPay](https://faucetpay.io/?r=44490) (referral link).

| Problem | Solution |
|---|---|
| The API returns only the last 100 payouts | `fpl payouts sync` keeps them all in a local SQLite file, without duplicates |
| How much does the faucet cost per day, per user, in EUR? | `fpl stats` |
| The faucet is empty and nobody noticed | `fpl watch` warns in Telegram or by exit code |
| What changed this month (tax return, bookkeeping)? | `fpl snapshot` daily, then `fpl report` |

You need a faucet: the API key belongs to a faucet, not to a normal user account.

## Example (made-up data)

```
$ fpl payouts sync
BTC    90 new payouts
LTC    60 new payouts
150 new payouts saved to C:\Users\you\.faucetpay-ledger\ledger.db

$ fpl stats --days 7
Payouts, last 7 days (EUR at current prices)
coin        payouts recipients             amount        EUR     avg amount    avg EUR
BTC              90         22         0.00000906       0.72     0.00000010   0.008053
LTC              60         17         0.00265000       0.20     0.00004417   0.003312

BTC by day (UTC)
day         payouts recipients             amount        EUR     avg amount    avg EUR
2026-11-20        7          5         0.00000068       0.05     0.00000010   0.007771
2026-11-21       31         17         0.00000322       0.26     0.00000010   0.008310
...

$ fpl watch --min BTC=0.000005,LTC=0.1
WARNING BTC: balance 0.0000021 is below 0.000005
WARNING BTC: balance 0.0000021 lasts about 14.1 h (limit 24 h)
```

## Install

Python 3.10+.

```bash
pip install git+https://github.com/PuchBuch/faucetpay-ledger
```

## Setup

1. FaucetPay → **Faucet Owner Dashboard** → your faucet → **Manage** → **API & Webhooks** → copy the API key.
2. Create a file `.env` with your settings (see [.env.example](.env.example)):

   ```ini
   FAUCETPAY_API_KEY=your-key
   ```

   It is read from the current folder, then from `~/.faucetpay-ledger/.env`
   (Windows: `C:\Users\you\.faucetpay-ledger\.env`). The second place is the right one for scheduled runs.
   Real environment variables win over both.

## Commands

```bash
fpl payouts sync                         # save new payouts of all coins
fpl payouts sync --currency BTC          # only one coin
fpl payouts --currency LTC --csv ltc.csv # just show/export the latest 100
fpl stats                                # last 30 days
fpl stats --month 2026-11 --csv nov.csv  # one month, also as CSV
fpl watch --min BTC=0.0005,LTC=0.1 --hours-left 24
fpl balances                             # balances and their value
fpl snapshot                             # save today's balances
fpl report --month 2026-11               # change per coin, needs snapshots
fpl --fiat usd stats                     # USD instead of EUR
```

`fpl watch` also syncs the payouts of the coins it checks. It warns if a balance is below `--min`, or if it
lasts less than `--hours-left` hours at the payout rate of the last 24 hours (default 24, `0` = off).

| Exit code of `fpl watch` | Meaning |
|---|---|
| 0 | fine, or the warning was handled by Telegram |
| 1 | the check failed (network, API key, Telegram) |
| 2 | warning, Telegram is not configured (also: wrong arguments) |

A failed check sends **no** Telegram message, so keep an eye on the exit code of your scheduled task.

## Telegram in 2 minutes

1. In Telegram, open **@BotFather**, send `/newbot`, and copy the token.
2. Send any message to your new bot.
3. Open `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `message.chat.id`.
4. Add both to `.env`:

   ```ini
   TELEGRAM_BOT_TOKEN=123456:ABC...
   TELEGRAM_CHAT_ID=123456789
   ```

The same warning is sent at most once in 6 hours.

## Run it on a schedule

**Windows Task Scheduler** (find the path with `(Get-Command fpl).Source`):

```powershell
schtasks /Create /TN "fpl watch" /SC MINUTE /MO 30 /TR "C:\path\to\fpl.exe watch --min BTC=0.0005"
schtasks /Create /TN "fpl sync" /SC HOURLY /TR "C:\path\to\fpl.exe payouts sync"
```

**cron:**

```cron
*/30 * * * * /path/to/fpl watch --min BTC=0.0005
0 * * * *    /path/to/fpl payouts sync
0 8 * * *    /path/to/fpl snapshot
```

Sync before 100 new payouts per coin have come together. If it was too late, `fpl` tells you that some may be missing.

## Good to know

- **EUR values in `stats` use today's price** (CoinGecko), not the price at payout time.
- **Payouts** have no id in the API. A payout is recognised by coin, recipient, amount and date;
  identical payouts in the same second are counted separately.
- **Amount unit.** The docs do not name the unit of payout amounts. The tool treats them as the coin's smallest
  unit, as for sending. The size of that unit is taken from `getbalance`; if it is not known yet, the
  amount is shown as `-` instead of a guess.
- **Dates** look like `02-05-26` and the docs do not say what comes first. The tool finds out from your data
  (a number above 12, or a date in the future) and tells you while it is not certain.
- **`report`:** start is the last snapshot before the month, end is the last one in it. Deposits and
  withdrawals are part of the change.

## Security

- **Read-only.** Only `currencies`, `getbalance` and `payouts` are called. There is no code that sends coins.
- **But the v1 key itself can send coins.** Treat it like a password, and use the IP whitelist of your faucet if you can.
- The API also has a **v2 with read-only keys**. This tool does not use it yet, because the docs do not describe
  the v2 responses of the read endpoints.
- Keys come only from the environment or `.env`, and are kept out of error messages.
- Data stays on your computer. The SQLite file contains the **recipients of your payouts** (emails or addresses),
  so keep it private.

## Development

```bash
pip install -e ".[test]"
pytest          # no network needed
ruff check .
```

## Related project

[faucetpay-merchant-demo](https://github.com/PuchBuch/faucetpay-merchant-demo): a Flask shop that accepts crypto
through FaucetPay Merchant.

## По-русски

Инструмент только для чтения для владельцев кранов FaucetPay: сохраняет выплаты в SQLite, считает статистику
по монетам и дням в EUR, предупреждает в Telegram, если баланс крана заканчивается. Монеты не отправляет.
Быстрый старт: установить, положить ключ в `.env`, затем `fpl payouts sync`, `fpl stats` и
`fpl watch --min BTC=0.0005`.

## Disclaimer

Not affiliated with FaucetPay. Crypto prices are volatile; this is not financial or tax advice.
The FaucetPay link above is my referral link – using it supports this project at no cost to you.

MIT License
