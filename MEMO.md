# MEMO — Excelta @ DigiKey Scraper & Analysis

**Date:** 2026-06-30 · **Status:** Built, tested, live-verified

## What it does
Pulls every Excelta product from DigiKey daily → dated CSV/XLSX → MBB-style analysis → (once history exists) trend/demand layer.

## Why API, not scraping
DigiKey storefront is Cloudflare-protected (curl → 403). Built on **DigiKey Product Information API v4** instead — sanctioned, reliable for unattended daily runs. Resolved hard limits by testing: `Offset+Limit ≤ 300` per query, empty-keyword requires a category filter, manufacturer = "Excelta Corporation" (id 2827). Categories >300 (tweezers 556, Tools 1296) are recursively subdivided by parametric filter.

## Latest run
**1,330 unique parts**, ~46s, 22 categories. CSV + formatted XLSX (Products + Summary).

## Key findings (T=0 baseline)
- **Value Pareto-extreme:** 157 SKUs (24%) = 80% of $501,745 visible inventory value.
- **Availability is the leak:** 51% stockout. Worst: screw/nut drivers 96%, wire cutters 76% (also #2 value line → priority).
- **Pricing wide, under-laddered:** median $54.84, max $2,427; knives category spread 788×; 150 quote-only ($0) SKUs.

## Deliverables
| Component | Output |
|---|---|
| `main.py` | `excelta_<date>.csv` + `.xlsx` (scrape) |
| `analyze.py` | `excelta_analysis_<date>.xlsx` (6 sheets + Pareto/stockout charts) + `ANALYSIS_<date>.md` |
| `dashboard.py` | `dashboard_<date>.html` — gorgeous, fully offline (ECharts inlined); trends section auto-activates at ≥2 snapshots |
| `timeseries.py` | `TRENDS_<date>.md` + `excelta_changes_<date>.csv` (gated: needs ≥2 snapshots) |
| tests | 15 offline tests pass |

## Discipline ("no mistakes")
Inventory value = list×stock **proxy** (not COGS). $0 excluded from price stats; qty=0 a real state, never imputed. **No trend/demand claims on one snapshot** — WS6 dormant until day 2.

## To operationalize
1. Cron `main.py` daily (line in README).
2. Run `analyze.py` for the deck; `timeseries.py` auto-fires once 2 snapshots exist.

## Open items
- App may be Sandbox-tier — confirm production approval for full live data.
- **Rotate the API secret** (it appeared in build chat).
- Demand proxy can't separate sale from stock adjustment — averages out over time; don't read single-day spikes as sales.
- Competitive benchmarking needs peer-manufacturer snapshots (future extension).
