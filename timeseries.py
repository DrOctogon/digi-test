#!/usr/bin/env python3
"""WS6 — time-series / demand layer. GATED: needs >=2 daily snapshots.

Reads every data/excelta_<date>.csv and computes day-over-day change between the
two most recent snapshots (plus cumulative event counts across all pairs):

  * new / discontinued SKUs (catalog flow)
  * restock events (qty 0 -> >0) and stockout events (qty >0 -> 0)
  * price changes (both prices > 0)
  * demand proxy = net units removed while in stock (NOT sell-through; a proxy that
    cannot distinguish a sale from a delisting/adjustment — labeled as such)

With a single snapshot it exits cleanly with a "gated" message. This module is the
dormant layer that activates automatically once history accrues.
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
from pathlib import Path

import config

DEMAND_PROXY_NOTE = ("demand proxy = net units removed between snapshots; a PROXY, "
                     "not confirmed sell-through (can't separate sales from adjustments)")


def snapshot_files(data_dir: Path = config.DATA_DIR) -> list[Path]:
    files = sorted(glob.glob(str(data_dir / "excelta_*.csv")))
    return [Path(f) for f in files if "analysis" not in os.path.basename(f)
            and "changes" not in os.path.basename(f)]


def _num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_index(csv_path: Path) -> dict[str, dict]:
    with csv_path.open(newline="", encoding="utf-8") as f:
        return {r["digikey_part_number"]: r for r in csv.DictReader(f)
                if r.get("digikey_part_number")}


def diff_pair(prev: dict[str, dict], cur: dict[str, dict]) -> dict:
    prev_keys, cur_keys = set(prev), set(cur)
    new = sorted(cur_keys - prev_keys)
    discontinued = sorted(prev_keys - cur_keys)
    changes, restocks, stockouts, price_moves = [], 0, 0, 0
    demand_units = 0
    for dk in sorted(cur_keys & prev_keys):
        pp, cp = _num(prev[dk]["unit_price"]), _num(cur[dk]["unit_price"])
        pq, cq = _num(prev[dk]["quantity_available"]), _num(cur[dk]["quantity_available"])
        pq = pq or 0
        cq = cq or 0
        event = ""
        if pq == 0 and cq > 0:
            event, restocks = "restock", restocks + 1
        elif pq > 0 and cq == 0:
            event, stockouts = "stockout", stockouts + 1
        price_delta = (round(cp - pp, 4) if pp and cp and pp > 0 and cp > 0 else None)
        if price_delta not in (None, 0):
            price_moves += 1
        units_moved = max(0, int(pq - cq)) if pq > cq else 0
        demand_units += units_moved
        if event or price_delta or units_moved:
            changes.append({
                "digikey_part_number": dk,
                "category_name": cur[dk].get("category_name", ""),
                "price_prev": pp, "price_cur": cp, "price_delta": price_delta,
                "qty_prev": int(pq), "qty_cur": int(cq), "qty_delta": int(cq - pq),
                "event": event, "units_moved_proxy": units_moved,
            })
    return {
        "new_skus": new, "discontinued_skus": discontinued,
        "restock_events": restocks, "stockout_events": stockouts,
        "price_moves": price_moves, "demand_units_proxy": demand_units,
        "changes": changes,
    }


def cumulative_events(files: list[Path]) -> dict:
    totals = {"restock_events": 0, "stockout_events": 0, "price_moves": 0,
              "demand_units_proxy": 0, "new_skus": 0, "discontinued_skus": 0, "pairs": 0}
    prev = None
    for f in files:
        cur = load_index(f)
        if prev is not None:
            d = diff_pair(prev, cur)
            totals["pairs"] += 1
            for k in ("restock_events", "stockout_events", "price_moves", "demand_units_proxy"):
                totals[k] += d[k]
            totals["new_skus"] += len(d["new_skus"])
            totals["discontinued_skus"] += len(d["discontinued_skus"])
        prev = cur
    return totals


def write_changes_csv(changes: list[dict], date: str, data_dir: Path = config.DATA_DIR) -> Path:
    cols = ["digikey_part_number", "category_name", "price_prev", "price_cur", "price_delta",
            "qty_prev", "qty_cur", "qty_delta", "event", "units_moved_proxy"]
    path = data_dir / f"excelta_changes_{date}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(changes)
    return path


def write_markdown(latest_pair: dict, cum: dict, prev_date: str, cur_date: str,
                   data_dir: Path = config.DATA_DIR) -> Path:
    d = latest_pair
    path = data_dir / f"TRENDS_{cur_date}.md"
    top_movers = sorted([c for c in d["changes"] if c["units_moved_proxy"] > 0],
                        key=lambda c: c["units_moved_proxy"], reverse=True)[:10]
    md = f"""# Excelta @ DigiKey — Trends ({prev_date} -> {cur_date})

> {DEMAND_PROXY_NOTE}.

## Latest day-over-day
| Metric | Value |
|---|--:|
| New SKUs | {len(d['new_skus'])} |
| Discontinued SKUs | {len(d['discontinued_skus'])} |
| Restock events (0->+) | {d['restock_events']} |
| Stockout events (+->0) | {d['stockout_events']} |
| Price changes | {d['price_moves']} |
| Demand proxy (units removed) | {d['demand_units_proxy']:,} |

## Cumulative across {cum['pairs']} snapshot pair(s)
| Metric | Total |
|---|--:|
| Restock events | {cum['restock_events']} |
| Stockout events | {cum['stockout_events']} |
| Price changes | {cum['price_moves']} |
| Demand proxy units | {cum['demand_units_proxy']:,} |
| New SKUs | {cum['new_skus']} |
| Discontinued SKUs | {cum['discontinued_skus']} |

## Top demand-proxy movers (latest day)
| DK part | Category | qty {prev_date} | qty {cur_date} | units removed |
|---|---|--:|--:|--:|
""" + ("\n".join(
        f"| {c['digikey_part_number']} | {c['category_name']} | {c['qty_prev']} | {c['qty_cur']} | {c['units_moved_proxy']} |"
        for c in top_movers) or "| (none yet) | | | | |") + """

## Guardrail
- Demand is a **proxy** — a net stock decrease, not a confirmed sale.
- Restock/stockout/price events are exact (derived from observed state changes).
"""
    path.write_text(md, encoding="utf-8")
    return path


def main(argv=None) -> int:
    argparse.ArgumentParser(description="WS6 time-series (needs >=2 snapshots)").parse_args(argv)
    files = snapshot_files()
    if len(files) < 2:
        print(f"GATED: only {len(files)} snapshot(s) in data/. "
              f"WS6 needs >=2. Run main.py on another day, then re-run.")
        return 0
    prev_f, cur_f = files[-2], files[-1]
    prev_date = prev_f.stem.replace("excelta_", "")
    cur_date = cur_f.stem.replace("excelta_", "")
    pair = diff_pair(load_index(prev_f), load_index(cur_f))
    cum = cumulative_events(files)
    csv_path = write_changes_csv(pair["changes"], cur_date)
    md_path = write_markdown(pair, cum, prev_date, cur_date)
    print(f"Compared {prev_date} -> {cur_date}: {len(pair['changes'])} changed SKUs")
    print(f"  -> {csv_path}")
    print(f"  -> {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
