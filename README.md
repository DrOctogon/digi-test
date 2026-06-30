# Excelta @ DigiKey — Daily Product Scraper

Pulls every **Excelta Corporation** product across the 31 DigiKey category/filter
links in `link.json` and writes one dated CSV per day to `data/excelta_YYYY-MM-DD.csv`.

Uses the **DigiKey Product Information API v4** (not HTML scraping): the storefront
sits behind Cloudflare, but `api.digikey.com` is a sanctioned REST API — reliable for
an unattended daily job.

## How it works

1. `config.py` parses `link.json` → 30 DigiKey category ids (the trailing path segment
   of each URL).
2. `digikey_client.py` does app-only OAuth (`client_credentials`, token cached to
   `.token_cache.json`) and v4 keyword search with retry/backoff.
3. `pipeline.py` fetches every category **concurrently** (`ThreadPoolExecutor`). DigiKey
   only exposes the first **300** matches per query, so any category larger than that
   (e.g. tweezers = 556) is **recursively subdivided by a parametric filter** until every
   slice fits. Results are merged with a global dedupe by DigiKey part number; leaf
   categories win the category label over broad parents.
4. `writer.py` writes the dated CSV (one row per DigiKey part number / variation).

Latest run: **1330 unique parts** in ~46s.

## Setup

```bash
uv venv && . .venv/bin/activate     # or: python -m venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt  # or: pip install -r requirements.txt
cp .env.example .env                # then fill in DIGIKEY_CLIENT_ID / _SECRET
```

Get credentials at <https://developer.digikey.com/> → **My Apps** → create an app with
the *Product Information* API enabled. The app starts in **Sandbox**; set
`DIGIKEY_API_BASE=https://sandbox-api.digikey.com` until your production app is approved.

`EXCELTA_MANUFACTURER_ID=2827` is pre-filled (resolved from "Excelta Corporation"). Leave
it blank to auto-resolve from `EXCELTA_MANUFACTURER_NAME` on first run.

## Usage

```bash
python main.py --probe          # verify creds, resolve manufacturer, sample one category
python main.py                  # full run -> data/excelta_<today>.csv
python main.py --date 2026-06-30 # override the date stamp
```

## CSV columns

`scrape_date, manufacturer, mfr_part_number, digikey_part_number, description,
detailed_description, category_id, category_name, unit_price, quantity_available,
min_order_qty, package_type, datasheet_url, product_url, photo_url, price_breaks_json,
parameters_json`

The two `*_json` columns hold the full price-break ladder and the long-tail parametric
attributes, so the schema stays stable across heterogeneous categories.

## Schedule it daily

This ships as a script only — wire up your own scheduler. macOS/Linux cron, 6am daily:

```cron
0 6 * * * cd /Users/tylerkrebs/vibe/digi-test && /Users/tylerkrebs/vibe/digi-test/.venv/bin/python main.py >> data/cron.log 2>&1
```

(`crontab -e` to add it. Use absolute paths — cron has a minimal environment.)

## Analysis (MBB-style baseline)

```bash
python analyze.py                 # latest snapshot -> XLSX (6 sheets + charts) + ANALYSIS_<date>.md
```
Outputs `data/excelta_analysis_<date>.xlsx` (Exec Summary, Data Quality, ABC, Stock Health,
Price Architecture, Assortment — with Pareto + stockout/value charts) and a Pyramid-Principle
markdown summary. Guardrails: inventory value is a list x stock **proxy**; $0 excluded from price
stats; no trend claims on one snapshot.

## Trends / demand (WS6 — needs >=2 snapshots)

```bash
python timeseries.py              # day-over-day: restock/stockout/price moves + demand proxy
```
Gated until two daily snapshots exist, then auto-produces `data/TRENDS_<date>.md` +
`data/excelta_changes_<date>.csv`. Demand = net units removed between snapshots (a **proxy**,
not confirmed sell-through).

## Tests

```bash
python -m pytest -q       # 15 offline tests (parser, analysis, time-series). No network/creds.
```

## Notes / limits

- DigiKey free tier ~1000 calls/day; a full run is ~120–180 calls (counts + pages +
  subdivision). Comfortably within budget for once-daily.
- Parent category **17 (Tools)** can't be subdivided and is capped at 300, but its parts
  are fully covered by the tool **leaf** categories — verified 0 unique parts lost.
- `.env` and `.token_cache.json` are gitignored. **Rotate the API secret** if this repo
  or any transcript containing it is shared.
