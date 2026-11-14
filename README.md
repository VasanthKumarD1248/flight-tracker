# Dublin ⇄ Chennai fare watch

Tracks round-trip economy fares DUB ⇄ MAA for departures 20–26 Feb 2027 with a 12–16 day trip
(35 date pairs), every 2 hours, and sends Telegram alerts when prices drop.

- **Dashboard:** GitHub Pages serves `docs/` (heatmap of every date pair, price history, all fares).
- **Checker:** `.github/workflows/check-prices.yml` runs `python -m tracker.check` on a schedule and
  commits results to `docs/data/`.
- **Data source:** Google Flights through the unofficial `fast-flights` library. It's free, but Google
  can change its page or block a run; after 3 empty runs in a row you get a Telegram warning.

## Alerts

| Alert | When |
|---|---|
| 🚨 BUY NOW | cheapest fare ≤ `strong_target_price` (€580) |
| ✅ Below your target | cheapest fare ≤ `target_price` (€600); repeats if it falls €5 more or every 12 h |
| 📉 New lowest price | lower than any price seen before |
| ⬇️ Price drop | at least `drop_alert_eur` (€10) below the lowest price in the last 24 h |
| ☀️ Daily summary | first check after 08:00 Dublin time |

Each rule runs for both the cheapest fare on any airline and the cheapest via Abu Dhabi (AUH).

## Change what's tracked

Edit `config.json` (dates, trip length, target prices, alert thresholds) and push. To check more
often, change the cron line in the workflow; every 2 hours is a deliberate balance against being
blocked.

## Run locally

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=... .venv/bin/python -m tracker.check
MAX_COMBOS=3 .venv/bin/python -m tracker.check   # quick test on 3 date pairs; no Telegram vars = print only
```
